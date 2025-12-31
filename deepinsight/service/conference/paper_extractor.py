# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# This module provides paper metadata extraction and persistence service
# for conference workflows. All logs and comments are in English.

from __future__ import annotations

import json
import logging
import traceback
from typing import List, Optional, Set, Tuple, Annotated, Dict, NamedTuple
from langchain_core.messages import HumanMessage
from pydantic import RootModel, Field, ValidationError
from os.path import abspath, dirname, join as join_path
import yaml

from sqlalchemy import and_, bindparam, delete, null, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from langchain.agents import create_agent
from langchain.agents.structured_output import AutoStrategy
from langchain_core.language_models import BaseChatModel
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_core.documents import Document
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from deepinsight.config.config import Config
from deepinsight.databases.connection import Database
from deepinsight.databases.models.academic import (
    Author as AuthorTable,
    Conference,
    Paper,
    PaperAuthorRelation,
)
from deepinsight.utils.llm_utils import (
    init_langchain_models_from_llm_config,
    parse_json_text_to_model,
)
from deepinsight.service.schemas.paper_extract import (
    ExtractPaperMetaRequest,
    ExtractPaperMetaResponse,
    ExtractPaperMetaFromDocsRequest,
    AuthorMeta,
    AuthorInfo,
    PaperMeta,
)
from deepinsight.service.conference.ror import RORClient
from deepinsight.utils.tavily_managed import default_tavily_key_manager
from deepinsight.utils.trace_utils import tracepoint


class PaperParseException(RuntimeError):
    """Exception that is safe to surface to clients."""


class _AuthorIdentify(NamedTuple):
    name: str
    email: str

    @staticmethod
    def from_author(author: AuthorMeta):
        """Load identify from Author object in PaperMeta."""
        return _AuthorIdentify(author.name, author.email)


class _Authorship(NamedTuple):
    author_id: int
    index: int
    is_corresponding: bool

class PaperExtractionService:
    """Service to extract paper metadata and persist to the database.

    - Extracts title, authors (with affiliations), abstract, keywords, topic
    - Resolves conference by `conference_id` or `knowledge_base_name` (SHORT+YEAR)
    - Creates/updates authors and paper records with relations
    """

    def __init__(self, config: Config):
        self._db = Database(config.database)
        self._config = config

    @staticmethod
    def _create_authorship(paper_meta: PaperMeta, author_ids: dict[_AuthorIdentify, int]) -> list[_Authorship]:
        deduplication_set: set[_AuthorIdentify] = set()
        ret = []
        for author in paper_meta.all_authors:
            identify = _AuthorIdentify.from_author(author)
            if identify in deduplication_set:
                continue
            deduplication_set.add(identify)
            ret.append(
                _Authorship(author_id=author_ids[identify], index=len(deduplication_set),
                            is_corresponding=author in paper_meta.author_info.corresponding_authors)
            )
        return sorted(ret, key=lambda item: item.index)

    @tracepoint(invisible_args="self")
    async def extract_and_store(self, req: ExtractPaperMetaRequest) -> ExtractPaperMetaResponse:
        """Extract paper metadata from Markdown and persist.
        Returns `ExtractPaperMetaResponse` with resulting paper and author IDs.
        """
        _, default_llm = init_langchain_models_from_llm_config(self._config.llms)

        conf_id, year, topics, existing_affiliations = await self._get_conference_and_affiliations(req)
        try:
            paper_meta = await self._parse_paper_meta(
                filename=req.filename,
                paper_content=req.paper,
                topics=topics or [],
                existing_affiliations=existing_affiliations,
                chat_model=default_llm,
            )
        except PaperParseException:
            raise
        except Exception as e:
            logging.error(f"Unexpected error while parsing paper metadata: {type(e).__name__}: {e}", exc_info=True)
            raise

        paper_id, author_ids = self._store_paper_meta(paper_meta, conf_id, year)
        return ExtractPaperMetaResponse(
            paper_id=paper_id,
            title=paper_meta.paper_title,
            conference_id=conf_id,
            author_ids=author_ids,
            topic=paper_meta.topic,
            full_meta=paper_meta,
        )

    async def extract_and_store_from_documents(self, req: ExtractPaperMetaFromDocsRequest) -> ExtractPaperMetaResponse:
        """Extract paper metadata from a list of parsed document segments and persist.
        Accepts content already split by a Document loader, avoiding re-parsing from raw files.
        """
        _, default_llm = init_langchain_models_from_llm_config(self._config.llms)
        conf_id, year, topics, existing_affiliations = await self._get_conference_and_affiliations(req)

        try:
            split_content = [seg.content for seg in req.documents if seg.content and seg.content.strip()]
            # Fallback: if empty, treat filename as content
            if not split_content:
                split_content = [req.filename]
            metadata = await self._extract_paper_metadata(
                split_content,
                topics or [],
                existing_affiliations,
                default_llm,
            )
        except PaperParseException:
            raise
        except Exception as e:
            logging.error(f"Unexpected error while parsing paper metadata (docs): {type(e).__name__}: {e}", exc_info=True)
            raise

        paper_id, author_ids = self._store_paper_meta(metadata, conf_id, year)
        return ExtractPaperMetaResponse(
            paper_id=paper_id,
            title=metadata.paper_title,
            conference_id=conf_id,
            author_ids=author_ids,
            topic=metadata.topic,
            full_meta=metadata,
        )

    # --------------------- Conference helpers ---------------------
    @tracepoint(invisible_args="self")
    async def _get_conference_and_affiliations(self, req: ExtractPaperMetaRequest) -> Tuple[int, int, List[str], Set[str]]:
        """Get conference ID/year/topics and existing affiliations.
        Returns: (conference_id, year, topics, existing_affiliations)
        """
        with self._db.get_session() as session:  # type: Session
            if req.conference_id:
                conf = session.query(Conference).filter(Conference.conference_id == req.conference_id).first()
                if not conf:
                    raise PaperParseException(f"Conference {req.conference_id} not found")
                affiliations = self._get_existing_affiliation(session, conf.conference_id)
                topics = conf.topics or []
                year = conf.year
                return conf.conference_id, year, topics, affiliations

        # Previously supported fallback to knowledge_base_name; removed per requirement
        raise PaperParseException("conference_id must be provided and valid")
        
    @staticmethod
    def _get_existing_affiliation(session: Session, conf_id: int) -> Set[str]:
        out = (
            session.execute(
                select(AuthorTable.affiliation)
                .join(PaperAuthorRelation, PaperAuthorRelation.author_id == AuthorTable.author_id)
                .join(Paper, PaperAuthorRelation.paper_id == Paper.paper_id)
                .where(
                    and_(
                        Paper.conference_id == conf_id,
                        AuthorTable.affiliation.is_not(None),
                        AuthorTable.affiliation != "",
                    )
                )
                .distinct()
            )
            .scalars()
            .all()
        )
        return set(out)

    # --------------------- Author & Paper persistence ---------------------
    @tracepoint(invisible_args="self")
    def _store_paper_meta(self, paper_meta: PaperMeta, conference_id: int, year: int) -> Tuple[int, List[int]]:
        """Create paper and author relations, or update existing paper authors if needed.
        Returns the `paper_id` and ordered `author_ids`.
        """
        author_ids = self._get_or_create_authors(conference_id, paper_meta)
        authorship_list = self._create_authorship(paper_meta, author_ids)
        if self._check_paper_exist_and_update(conference_id, paper_meta, authorship_list):
            # fetch paper_id for response
            with self._db.get_session() as session:
                paper = session.query(Paper).filter(
                    and_(Paper.conference_id == conference_id, Paper.title == paper_meta.paper_title)
                ).first()
                return paper.paper_id, [authorship.author_id for authorship in authorship_list]

        # Persist new paper
        paper = Paper(
            title=paper_meta.paper_title,
            conference_id=conference_id,
            publication_year=year,
            abstract=paper_meta.abstract,
            keywords=",".join(paper_meta.keywords or []),
            topic=paper_meta.topic,
            author_ids=json.dumps([authorship.author_id for authorship in authorship_list]),
        )
        try:
            with self._db.get_session() as session:  # type: Session
                session.add(paper)
                session.flush()
                if authorship_list:
                    session.add_all(
                        PaperAuthorRelation(paper_id=paper.paper_id, author_id=authorship.author_id, 
                            author_order=authorship.index, is_corresponding=authorship.is_corresponding
                        )
                        for authorship in authorship_list
                    )
                session.commit()
                return paper.paper_id, [authorship.author_id for authorship in authorship_list]
        except Exception as e:
            logging.error(f"Failed to store paper metadata {paper} with {type(e).__name__}: {e}", exc_info=True)
            raise PaperParseException("Failed to persist paper metadata") from e

    def _get_or_create_authors(self, conference_id: int, paper: PaperMeta) -> Dict[_AuthorIdentify, int]:
        """Get if exist and create otherwise for every author in `paper.author_info`.

        Returns a dict from (author_name, author_email) to author ID with deduplication."""
        deduplication_map = {}
        
        author_list = []
        # remove empty and duplicated authors.
        for author in paper.all_authors:
            identify = _AuthorIdentify.from_author(author)
            if identify in deduplication_map:
                if author != deduplication_map[identify]:
                    logging.warning(f"{author!r} has the same name and email with {deduplication_map[identify]!r} in "
                                    f"the same paper {paper.paper_title!r} with different content. Only the later one"
                                    " selected.")
                continue
            deduplication_map[identify] = author
            author_list.append(author)

        if not author_list:
            logging.warning(f"Not found any author in paper {paper.paper_title!r}.")
            return {}

        max_retry = 5

        while max_retry:
            max_retry -= 1
            author_ids = self._get_or_create_authors_single(author_list, conference_id)
            if author_ids:
                return author_ids
        logging.error("Try create new author with too many conflicts.")
        raise RuntimeError("Try create new author with too many conflicts.")

    def _get_or_create_authors_single(self, author_list: list[AuthorMeta], conf_id: int) -> dict[_AuthorIdentify, int]:
        author_names = [author.name for author in author_list]
        author_emails = [author.email for author in author_list]
        author_lookup_table = {_AuthorIdentify.from_author(author): author for author in author_list}
        with self._db.get_session() as session:  # type: Session
            author_rows = session.execute(
                select(AuthorTable.author_id, AuthorTable.author_name, AuthorTable.email)
                .where(and_(
                    AuthorTable.conference_id == conf_id,
                    AuthorTable.author_name.in_(author_names),
                    AuthorTable.email.in_(author_emails)
                ))
            ).all()
            existing_authors: dict[_AuthorIdentify, int] = {
                _AuthorIdentify(name, email): id_ for
                (id_, name, email) in author_rows
                if _AuthorIdentify(name, email) in author_lookup_table
            }
            if set(author_lookup_table) == set(existing_authors):
                self._update_existing_authors(session, author_lookup_table, existing_authors)
                return existing_authors

            need_creates = []
            for key in set(author_lookup_table) - set(existing_authors):
                new_author = author_lookup_table[key]
                need_creates.append(AuthorTable(conference_id=conf_id,
                                                author_name=new_author.name, email=new_author.email,
                                                affiliation=new_author.affiliation,
                                                affiliation_country=new_author.affiliation_country,
                                                affiliation_city=new_author.affiliation_city))

            try:  # create with retry
                session.add_all(need_creates)
                session.commit()
            except IntegrityError:
                logging.info("Try create new author with conflict, retry...")
                return {}
            except Exception as e:
                logging.error(f"Try query author info with {type(e).__name__}, canceled: {e}", exc_info=True)
                raise

            existing_authors.update({_AuthorIdentify(author.author_name, author.email): author.author_id
                                     for author in need_creates})
            self._update_existing_authors(session, author_lookup_table, existing_authors)
        return existing_authors

    @staticmethod
    def _update_existing_authors(
        session: Session,
        author_lookup_table: dict[_AuthorIdentify, AuthorMeta],
        existing_authors: dict[_AuthorIdentify, int],
    ) -> None:
        """Update null/empty affiliation fields for existing authors."""
        authors = [(author_lookup_table[key], id_) for (key, id_) in existing_authors.items()]
        to_update_values = [
            dict(
                author_id=id_,
                new_affiliation=a.affiliation,
                new_affiliation_country=a.affiliation_country,
                new_affiliation_city=a.affiliation_city,
            )
            for a, id_ in authors
            if a.affiliation or a.affiliation_city or a.affiliation_country
        ]
        if not to_update_values:
            logging.debug("No authors to update")
            return
        session.execute(
            update(AuthorTable)
            .where(or_(AuthorTable.affiliation.is_(null()), AuthorTable.affiliation == ""))
            .values(
                affiliation=bindparam("new_affiliation"),
                affiliation_country=bindparam("new_affiliation_country"),
                affiliation_city=bindparam("new_affiliation_city"),
            ),
            to_update_values,
            execution_options={"synchronize_session": False},
        )
        session.commit()
        logging.info(f"Updated affiliation information for about {len(authors)} authors")

    def _check_paper_exist_and_update(self, conference_id: int, paper_meta: PaperMeta,
                                      new_authorship: list[_Authorship]) -> bool:
        """Return `True` if it is an existing paper.

        What's more:
        - If the given authorship from `new_authorship` is different from the existing one on DB, update them;
        - If the given topic is different from the existing topic on DB, update topic.
        """
        with self._db.get_session() as session:  # type: Session
            paper: Paper | None = session.query(Paper).filter(
                and_(Paper.conference_id == conference_id, Paper.title == paper_meta.paper_title)
            ).first()
            if paper is None:
                return False
            authorship_in_db: Iterable[PaperAuthorRelation] = session.execute(
                select(PaperAuthorRelation)
                .where(PaperAuthorRelation.paper_id == paper.paper_id)  # type: ignore
            ).scalars().all()
            existing_authorship = set(
                _Authorship(item.author_id, item.author_order, item.is_corresponding) for item in authorship_in_db
            )
            if existing_authorship != set(new_authorship):
                session.execute(
                    delete(PaperAuthorRelation).where(PaperAuthorRelation.paper_id == paper.paper_id)  # type: ignore
                )
                if new_authorship:
                    session.add_all(
                        PaperAuthorRelation(paper_id=paper.paper_id, author_id=item.author_id, author_order=item.index,
                                            is_corresponding=item.is_corresponding)
                        for item in new_authorship
                    )
                session.commit()
            if paper.topic != paper_meta.topic:
                paper.topic = paper_meta.topic
                session.commit()
            return True

    # --------------------- Parsing with LLM ---------------------
    async def _parse_paper_meta(
        self,
        filename: str,
        paper_content: str,
        topics: List[str],
        existing_affiliations: Set[str],
        chat_model: BaseChatModel,
    ) -> PaperMeta:
        """Parse paper content into structured metadata using an LLM prompt."""
        document = self._split_markdown_content(paper_content)
        split_content = [block.page_content for block in document]
        metadata = await self._extract_paper_metadata(split_content, topics, existing_affiliations, chat_model)
        if not metadata.paper_title:
            metadata.paper_title = filename
        return metadata

    @staticmethod
    def _split_markdown_content(markdown_content: str) -> List[Document]:
        """
        Extract headings from Markdown and split content by headings.
    
        - Preserve all heading lines in the output content.
        - Split only on levels 1–4 (do not split level 5 or deeper).
    
        Args:
            markdown_content: The Markdown text to split.
    
        Returns:
            List of content blocks as LangChain Documents. Each block keeps the heading
            text, and its metadata includes keys "Header 1" to "Header 4" for present
            heading levels (1 to 4).
        """
        headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
            ("####", "Header 4"),
        ]
        splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            strip_headers=False,
        )
        return splitter.split_text(markdown_content)

    @tracepoint(invisible_args=["self", "chat_model"])
    async def _extract_paper_metadata(
        self,
        split_content: List[str],
        topics: List[str],
        existing_affiliations: Set[str],
        chat_model: BaseChatModel,
    ) -> PaperMeta:
        """Extract metadata from up to the first two markdown blocks using unified retry.
        Builds context and invokes LLM with structured parsing into PaperMeta.
        """
        num_context_blocks = min(2, len(split_content))
        content = "\n\n".join([block for block in split_content[:num_context_blocks]])

        parser = PydanticOutputParser(pydantic_object=PaperMeta)
        prompt: PromptTemplate = PromptTemplate.from_template(
            _METADATA_EXTRACT_PROMPT
        )
        chain = prompt | chat_model | parser

        # Build affiliation list as bullet points for prompt interpolation
        affiliation_list = "\n".join("- " + json.dumps(a, ensure_ascii=False) for a in existing_affiliations)

        try:
            llm_meta: PaperMeta = await chain.with_retry().ainvoke({
                "context": content,
                "topics": json.dumps(topics, ensure_ascii=False),
                "existing_affiliation": affiliation_list,
            })
        except Exception as e:
            logging.error(
                f"Failed to extract metadata via LLM: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise PaperParseException("Failed to parse LLM structured output")

        # 机构矫正统一通过 Agent，重试逻辑与论文解析一致
        llm_meta:PaperMeta = await self._correct_affiliation_names(llm_meta, chat_model)

        has_empty = False
        if llm_meta.author_info.first_author is not None:
            if not (llm_meta.author_info.first_author.name or llm_meta.author_info.first_author.email):
                has_empty = True
                llm_meta.author_info.first_author = None
        for author_list in (
            llm_meta.author_info.co_first_authors,
            llm_meta.author_info.middle_authors,
            llm_meta.author_info.last_authors,
            llm_meta.author_info.corresponding_authors,
        ):
            for author in author_list[:]:
                if author.name or author.email:
                    continue
                author_list.remove(author)
                has_empty = True
        if has_empty:
            logging.info(f"paper parsed result (removed empty): {llm_meta}")

        llm_meta = await self._unify_country_name(chat_model, llm_meta)
        return llm_meta

    class _AffiliationMap(RootModel):
        root: dict[Annotated[str, Field(min_length=1)], Annotated[str, Field(min_length=1)]]

    @tracepoint(invisible_args=["self", "chat_model"])
    async def _correct_affiliation_names(
        self,
        llm_meta: PaperMeta,
        chat_model: BaseChatModel,
    ) -> PaperMeta:
        """Use an agent to correct institution names with unified retry.
        Returns the updated `PaperMeta` where author affiliations are standardized.
        """
        affiliations = set(a.affiliation for a in llm_meta.all_authors if a.affiliation)
        if not affiliations:
            return llm_meta

        try:
            search_tool = default_tavily_key_manager().tool()
            agent = create_agent(
                model=chat_model,
                tools=[search_tool],
                system_prompt=_FIX_AFFILIATION_SYSTEM_PROMPT_TEXT,
            )
            names = json.dumps(list(affiliations), ensure_ascii=False)
            result = await agent.with_retry().ainvoke(
                input=dict(
                    messages=[HumanMessage(content=f"Fix these names into their full legal English name of the organization registered\n:{names}")],
                ),
            )

            # 从 agent 返回的 messages 中拿到最后一条 AI 回复内容，再从中解析 JSON
            messages = result.get("messages") if isinstance(result, dict) else None
            if not messages:
                logging.warning("Affiliation correction agent returned no messages.")
                return llm_meta

            last_msg = messages[-1]
            content = getattr(last_msg, "content", None)

            # LangChain 有时会返回 list[dict] 结构的 content，这里做一次统一
            if isinstance(content, list):
                try:
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                except Exception:
                    content = str(content)

            if not isinstance(content, str) or not content.strip():
                logging.warning(
                    "Affiliation correction agent last message has invalid content: "
                    f"type={type(content).__name__}"
                )
                return llm_meta

            # 使用通用工具：从文本中提取 JSON 并解析为 RootModel
            mapping_model = parse_json_text_to_model(content, self._AffiliationMap)
            mapping = mapping_model.root
        except Exception as e:
            logging.warning(
                f"Affiliation correction agent invocation failed: {type(e).__name__}: {e}",
                exc_info=True,
            )
            logging.error(traceback.format_exc())
            return llm_meta

        mapping = await self._fix_by_ror(mapping, chat_model)

        for author in llm_meta.all_authors:
            try:
                if author.affiliation and author.affiliation in mapping:
                    corrected = mapping[author.affiliation]
                    if isinstance(corrected, str) and corrected:
                        author.affiliation = corrected
            except Exception as parse_err:
                logging.warning(f"Affiliation correction parse failed: {type(parse_err).__name__}: {parse_err}")
        return llm_meta

    async def _fix_by_ror(self, mapping: dict[str, str], llm: BaseChatModel) -> dict[str, str]:
        to_fix_by_ror = set(mapping.values())
        client = RORClient(verify_ssl=False)
        fixed_by_ror = {name: await client.match_one_or_origin(name, llm=llm) for name in to_fix_by_ror}
        log_str = "\n".join(f"{origin!r} => {mapping[origin]!r} => {fixed_by_ror[mapping[origin]]!r}" for origin in mapping)
        logging.info(f"Affiliation mapping of this paper:\n{log_str}")
        return {origin: fixed_by_ror[llm_fixed] for origin, llm_fixed in mapping.items()}

    @tracepoint(invisible_args=["self", "chat_model"])
    async def _unify_country_name(self, chat_model: BaseChatModel, paper_meta: PaperMeta) -> PaperMeta:
        to_correct: set[str] = set()

        for author in paper_meta.all_authors:
            if (not author.affiliation_country) or (author.affiliation_country in _COUNTRY_NAME_SET):
                continue
            if author.affiliation_country in _COUNTRY_NAME_MAP:
                author.affiliation_country = _COUNTRY_NAME_MAP[author.affiliation_country]
                continue
            to_correct.add(author.affiliation_country)
        if not to_correct:  # unmatch may because of country is null or empty
            return paper_meta
        corrected = await self._unify_country_name_by_llm(chat_model, to_correct)
        for author in paper_meta.all_authors:
            if author.affiliation_country in corrected:
                author.affiliation_country = corrected[author.affiliation_country]
        return paper_meta

    @tracepoint(invisible_args=["self", "chat_model"])
    async def _unify_country_name_by_llm(self, chat_model: BaseChatModel, to_correct: set[str]) -> dict[str, str]:
        to_correct = set(to_correct)
        retry_count = 3
        corrected = dict()
        for _ in range(retry_count):
            prompt = (PromptTemplate(template=_COUNTRY_FIX_PROMPT, input_variables=["context"])
                    .format_prompt(context=json.dumps(list(to_correct))).to_string())
            try:
                llm_output = (await chat_model.ainvoke(prompt)).content
            except Exception as e:
                logging.error(f"修正国家名称时，调用LLM发生异常{type(e).__name__}: {e}", exc_info=True)
                continue
            left = llm_output.find("{")
            right = llm_output.rfind("}")
            if left == -1 or right == -1:
                logging.error(f"LLM生成的{llm_output!r}不包含完整的json对象")
                continue
            maybe_json_str = llm_output[left:right + 1]
            try:
                correcting_map = _StrDict.model_validate_json(maybe_json_str).root
            except ValidationError:
                logging.error(f"修正国家名称时，LLM生成的映射{maybe_json_str!r}无法通过JSON校验。LLM输出为：{llm_output!r}",
                            exc_info=True)
                continue

            # check mapping legal
            if to_correct == set(correcting_map.keys()) and all(v in _COUNTRY_NAME_SET for v in correcting_map.values()):
                corrected.update(correcting_map)
                return corrected
            for old, new in correcting_map.items():
                if (old not in to_correct) or (new not in _COUNTRY_NAME_SET):
                    continue
                to_correct.remove(old)
                corrected[old] = new
            if not to_correct:
                return corrected

        logging.warning(f"Attempting to correct these country names has reached max retry limit: {to_correct}. Skip.")
        for v in to_correct:
            corrected[v] = v
        return corrected

class _Iso31661File(RootModel):
    class _Line(NamedTuple):
        alpha2: Annotated[str, Field(pattern=r"^[A-Z]{2,2}$")]
        alpha3: Annotated[str, Field(pattern=r"^[A-Z]{3,3}$")]
        short_name: str

    root: list[_Line]

class _StrDict(RootModel):
    root: dict[str, str]

def _create_country_map():
    with open(join_path(dirname(abspath(__file__)), "iso-3166-1.yaml")) as f:
        origin_object = yaml.safe_load(f)
    iso3166_1_table = _Iso31661File.model_validate(origin_object).root
    result_map = {item.short_name: item.short_name for item in iso3166_1_table}
    result_map.update({item.alpha2: item.short_name for item in iso3166_1_table})
    result_map.update({item.alpha3: item.short_name for item in iso3166_1_table})
    result_map.update({  # special cases
        "United States": "United States of America",
        "UK": "United Kingdom",
        "North Korea": "Korea, Democratic People's Republic of",
        "South Korea": "Korea, Republic of"
    })
    return result_map, set(item.short_name for item in iso3166_1_table)


_COUNTRY_NAME_MAP, _COUNTRY_NAME_SET = _create_country_map()
_COUNTRY_NAME_MAP: dict[str, str]
_COUNTRY_NAME_SET: set[str]
_COUNTRY_FIX_PROMPT = """
## Role
You are an country name correction agent familiar with ISO 3166-1 standard.  
Your task is to correct each country name in the given list to the standardized names as specified in ISO 3166-1 and \
represent the mapping between the original and corrected names using a JSON object.

## Task
Correct each given country popular name into ISO 3166-1 standard short name using comma format.
You need to ensure that every output always uses the names specified in the ISO 3166-1 standard.
for example, you should output "Korea, Republic of" instead of "Korea (Republic of)" or "Republic of Korea" or \
anything else for a input "South Korea".
Return a json object mapping from input common name to the corrected name in ISO 3166-1 short name (comma format).

## Context
The country names to be corrected:
{context}

## Correction Guidelines

## Output Format
Return your answer strictly following JSON object structure

---

## Example

### Input
["Korea (Republic of)", "Hong Kong, SAR"]

### Output
{{
    "Korea (Republic of)": "Korea, Republic of",
    "Hong Kong, SAR": "Hong Kong",
    "UK": "United Kingdom"
}}
"""

_METADATA_EXTRACT_PROMPT = """
## Role
You are an expert academic metadata extraction agent.
Your task is to read the given paper text and extract structured metadata in a strict JSON format.
When outputting JSON, escape all backslashes and LaTeX characters so that it is valid JSON. For every "\\" output "\\\\".

## Task
Extract the following metadata fields from the provided paper content:
1. Paper title
2. Author information — distinguish **first author**, **co-first authors**, **middle authors**, **last author**, and **corresponding authors**.
   Each author must include:
   - name
   - email
   - address
   - affiliation
   - affiliation_country
   - affiliation_city
3. Abstract
4. Keywords (as a list)
5. topic

## Context
Paper content:
{context}

## Extraction Guidelines
- "topic" can only be selected between {topics}.
- **Corresponding authors** are usually marked with symbols such as `*` or `†`. Identify them accurately.
- Preserve the correct **author order**.
- Leave any missing information fields empty.
- **Affiliation normalization**:
  - Convert all institution names into standard **English** forms. If they appear in another language (e.g., Chinese, French, German), translate them to their standard English equivalents.
- **Affiliation disambiguation rules**:
  1. **Parallel (peer) affiliations** — e.g., “University A; Institute B” — keep **only the most influential institution** (by academic ranking or global reputation).
  2. **Hierarchical affiliations** — e.g., “Institute of Microelectronics, Chinese Academy of Sciences” — keep **only the highest-level institution** (in this case, “Chinese Academy of Sciences”).
  3. If uncertain, follow this priority order: **University > Academy > Institute > Laboratory**.
- **Affiliation English Standardization Rules**:
  - Translate all institution names to their **official English equivalents**.
  - Use consistent capitalization and remove redundant words unless part of the official name.
- For "affiliation_country", all values should be in ISO-3166-1 English short name format.
- If no keywords are explicitly listed, extract **up to 8 keywords** from the abstract.
- Return **only** the final JSON output — no explanations or commentary.

## Existing Affiliation Organization Names
{existing_affiliation}

## Output Format
Return your answer strictly following this JSON structure:

{{
    "paper_title": "",
    "author_info": {{
        "first_author": {{
            "name": "",
            "email": "",
            "address": "",
            "affiliation": "",
            "affiliation_country": "",
            "affiliation_city": ""
        }},
        "co_first_authors": [{{"name": "", "email": "", "address": "", "affiliation": "", "affiliation_country": "", "affiliation_city": ""}}],
        "middle_authors": [{{"name": "", "email": "", "address": "", "affiliation": "", "affiliation_country": "", "affiliation_city": ""}}],
        "last_authors": [{{"name": "", "email": "", "address": "", "affiliation": "", "affiliation_country": "", "affiliation_city": ""}}],
        "corresponding_authors": [{{"name": "", "email": "", "address": "", "affiliation": "", "affiliation_country": "", "affiliation_city": ""}}]
    }},
    "abstract": "",
    "topic": "",
    "keywords": []
}}
"""

# 机构矫正的系统提示词（作者单位标准化）
_FIX_AFFILIATION_SYSTEM_PROMPT_TEXT = """
## Role
You are an organization name verification agent familiar.  
Your task is to correct each organization name (may with department info) into their universal and human friendly name \
(organization name only) in English.

## Task in 5 steps
1. Expand the abbreviations into the most commonly used organizational names at academic conferences.
2. Removing any regional division information and department information. When this organization is part of \
the U.S.State University System, do not remove its regional division.
3. Correct each string after step 1 into an official organization name.
4. Return a json object mapping from input to the corrected name.
5. Removing corporate legal structure like "Ltd.", "Corp.", "Inc" for companies and nationality information \
of multinational corporations.

You need to ensure that every output always is an Academy / University / Institute / Laboratory / Company name \
which is a registered full legal name in English, excluding any regional division information and department \
information.

For example:
- You should output "Shanghai Jiao Tong University" for the input "School of Computer Science, Shanghai Jiao Tong \
University" because the input includes information about institutions and departments (schools), but only the \
institution name is needed.
- You should output "The Chinese University of Hong Kong" for the input "The Chinese University of Hong Kong, Shenzhen"\
  because "Shenzhen" is regional division information which is not needed.
- You should output "Huawei Technologies" or "Huawei" instead of "Huawei Technologies Co., Ltd." or anything else for \
  an input "Huawei Tech. Co.,Ltd. (Shenzhen)", because "Shenzhen" is a regional division information which is not \
  needed and "Co.,Ltd." is corporate legal structure which is no needed.
  Similarly, for "Synopsys Inc." and "Synopsys Korea", you should output "Synopsys" because that is its \
  human-friendly name without corporate legal structure and nationality information.
- You should return "University of California, Los Angeles" for input "University of California, Los Angeles" because
  this organization belongs to US State University System (keep its regional division).


Be carefully that your output **should be a valid json**.

## Correction Guidelines
- Removing department (including schools) info and regional division info before checking if it is the common English\
 name of the organization without unnecessary parts. Keep regional division of all organizations belongs to US State\
 University System;
- Return full common English name of the organization;
- Using searching tools if necessary;

## Output Format
Return your answer strictly following JSON object structure

---

## Example

### Input
["Harbin Institute of Technology (Shenzhen)", "University of California, San Diego", "imec", \
"Ulsan National Institute of Science and Technology (UNIST)"]

### Output
{{
    "Harbin Institute of Technology (Shenzhen)": "Harbin Institute of Technology",
    "University of California, San Diego": "University of California, San Diego",
    "imec": "Interuniversity Microelectronics Centre",
    "Ulsan National Institute of Science and Technology (UNIST)": "Ulsan National Institute of Science and Technology"
}}
"""