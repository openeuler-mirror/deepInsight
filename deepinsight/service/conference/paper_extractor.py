# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# This module provides paper metadata extraction and persistence service
# for conference workflows. All logs and comments are in English.

from __future__ import annotations

import json
import logging
import traceback
from typing import List, Optional, Set, Tuple, Annotated
from langchain_core.messages import HumanMessage
from pydantic import RootModel, Field

from sqlalchemy import and_, bindparam, delete, null, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_core.documents import Document
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_tavily import TavilySearch

from deepinsight.config.config import Config
from deepinsight.databases.connection import Database
from deepinsight.databases.models.academic import (
    Author as AuthorTable,
    Conference,
    Paper,
    PaperAuthorRelation,
)
from deepinsight.utils.llm_utils import init_langchain_models_from_llm_config
from deepinsight.service.schemas.paper_extract import (
    ExtractPaperMetaRequest,
    ExtractPaperMetaResponse,
    ExtractPaperMetaFromDocsRequest,
    AuthorMeta,
    AuthorInfo,
    PaperMeta,
)


class PaperParseException(RuntimeError):
    """Exception that is safe to surface to clients."""


class PaperExtractionService:
    """Service to extract paper metadata and persist to the database.

    - Extracts title, authors (with affiliations), abstract, keywords, topic
    - Resolves conference by `conference_id` or `knowledge_base_name` (SHORT+YEAR)
    - Creates/updates authors and paper records with relations
    """

    def __init__(self, config: Config):
        self._db = Database(config.database)
        self._config = config

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
        )

    # --------------------- Conference helpers ---------------------
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
    def _store_paper_meta(self, paper_meta: PaperMeta, conference_id: int, year: int) -> Tuple[int, List[int]]:
        """Create paper and author relations, or update existing paper authors if needed.
        Returns the `paper_id` and ordered `author_ids`.
        """
        author_ids = self._get_or_create_authors(paper_meta)
        if self._check_paper_exist_and_update(conference_id, paper_meta.paper_title, author_ids):
            # fetch paper_id for response
            with self._db.get_session() as session:
                paper = session.query(Paper).filter(
                    and_(Paper.conference_id == conference_id, Paper.title == paper_meta.paper_title)
                ).first()
                return paper.paper_id, author_ids  # type: ignore

        # Persist new paper
        paper = Paper(
            title=paper_meta.paper_title,
            conference_id=conference_id,
            publication_year=year,
            abstract=paper_meta.abstract,
            keywords=",".join(paper_meta.keywords or []),
            topic=paper_meta.topic,
            author_ids=json.dumps(author_ids),
        )
        try:
            with self._db.get_session() as session:  # type: Session
                session.add(paper)
                session.flush()
                session.add_all(
                    PaperAuthorRelation(paper_id=paper.paper_id, author_id=id_, author_order=index)
                    for index, id_ in enumerate(author_ids, 1)
                )
                session.commit()
                return paper.paper_id, author_ids
        except Exception as e:
            logging.error(f"Failed to store paper metadata {paper} with {type(e).__name__}: {e}", exc_info=True)
            raise PaperParseException("Failed to persist paper metadata") from e

    def _get_or_create_authors(self, paper: PaperMeta) -> List[int]:
        """Ensure all authors exist; create missing ones; return ordered IDs with deduplication."""
        dedup = set()
        authors: List[AuthorMeta] = []
        for a in paper.all_authors:
            if a is None or not any((a.name, a.email, a.address)):
                continue
            dumped = a.model_dump_json()
            if dumped in dedup:
                continue
            dedup.add(dumped)
            authors.append(a)

        if not authors:
            raise PaperParseException("No author information extracted from paper")

        max_retry = 5
        while max_retry:
            max_retry -= 1
            ids = self._get_or_create_authors_single(authors)
            if ids:
                return ids
        logging.error("Too many conflicts while creating authors")
        raise RuntimeError("Too many conflicts while creating authors")

    def _get_or_create_authors_single(self, author_list: List[AuthorMeta]) -> List[int]:
        names = [a.name for a in author_list]
        emails = [a.email for a in author_list]
        lookup = {(a.name, a.email): a for a in author_list}
        with self._db.get_session() as session:  # type: Session
            rows = session.execute(
                select(AuthorTable.author_id, AuthorTable.author_name, AuthorTable.email)
                .where(and_(AuthorTable.author_name.in_(names), AuthorTable.email.in_(emails)))
            ).all()
            existing = {(name, email): id_ for (id_, name, email) in rows}
            if set(lookup).issubset(existing):
                self._update_existing_authors(session, lookup, existing)
                return [existing[k] for k in lookup]

            to_create = []
            for key in set(lookup) - set(existing):
                a = lookup[key]
                to_create.append(
                    AuthorTable(
                        author_name=a.name,
                        email=a.email,
                        affiliation=a.affiliation,
                        affiliation_country=a.affiliation_country,
                        affiliation_city=a.affiliation_city,
                    )
                )

            try:
                session.add_all(to_create)
                session.commit()
            except IntegrityError:
                logging.info("Author creation conflict, retrying...")
                return []
            except Exception as e:
                logging.error(f"Unexpected error creating authors: {type(e).__name__}: {e}", exc_info=True)
                raise

            existing.update({(a.author_name, a.email): a.author_id for a in to_create})
            self._update_existing_authors(session, lookup, existing)
        return [existing[k] for k in lookup]

    @staticmethod
    def _update_existing_authors(
        session: Session,
        author_lookup_table: dict[tuple[str, Optional[str]], AuthorMeta],
        existing_authors: dict[tuple[str, Optional[str]], int],
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

    def _check_paper_exist_and_update(self, conference_id: int, title: str, new_author_ids: List[int]) -> bool:
        """Return True if an existing paper was found (and updated if needed)."""
        with self._db.get_session() as session:  # type: Session
            paper: Optional[Paper] = (
                session.query(Paper)
                .filter(and_(Paper.conference_id == conference_id, Paper.title == title))
                .first()
            )
            if paper is None:
                return False
            authors_in_db = (
                session.execute(select(PaperAuthorRelation.author_id).where(PaperAuthorRelation.paper_id == paper.paper_id))
                .scalars()
                .all()
            )
            if set(authors_in_db) == set(new_author_ids):
                return True
            session.execute(delete(PaperAuthorRelation).where(PaperAuthorRelation.paper_id == paper.paper_id))
            session.add_all(
                PaperAuthorRelation(paper_id=paper.paper_id, author_id=id_, author_order=index)
                for index, id_ in enumerate(new_author_ids, 1)
            )
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
        llm_meta = await self._correct_affiliation_names(llm_meta, chat_model)
        return llm_meta

    class _AffiliationMap(RootModel):
        root: dict[Annotated[str, Field(min_length=1)], Annotated[str, Field(min_length=1)]]

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
            search_tool = TavilySearch()
            agent = create_agent(
                model=chat_model,
                tools=[search_tool],
                system_prompt=_FIX_AFFILIATION_SYSTEM_PROMPT_TEXT,
                response_format=ToolStrategy(self._AffiliationMap),
            )
            names = json.dumps(list(affiliations), ensure_ascii=False)
            result = await agent.with_retry().ainvoke(
                input=dict(
                    messages=[HumanMessage(content=f"Fix these names into their full legal English name of the organization registered\n:{names}")],
                ),
            )
            mapping = result["structured_response"]
        except Exception as e:
            logging.warning(
                f"Affiliation correction agent invocation failed: {type(e).__name__}: {e}",
                exc_info=True,
            )
            logging.error(traceback.format_exc())
            return llm_meta

        if isinstance(mapping, self._AffiliationMap):
            mapping = mapping.root
        else:
            logging.warning(f"Affiliation correction output is not a valid map (type={type(mapping).__name__})")
            return llm_meta

        for author in llm_meta.all_authors:
            try:
                if author.affiliation and author.affiliation in mapping:
                    corrected = mapping[author.affiliation]
                    if isinstance(corrected, str) and corrected:
                        author.affiliation = corrected
            except Exception as parse_err:
                logging.warning(f"Affiliation correction parse failed: {type(parse_err).__name__}: {parse_err}")
        return llm_meta


_METADATA_EXTRACT_PROMPT = """
## Role
You are an expert academic metadata extraction agent.
Your task is to read the given paper text and extract structured metadata in a strict JSON format.

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
Your task is to correct each organization name (may with department info) into their official name \
(organization name only) in English.

## Task in 3 steps
1. Removing any regional division information and department information.
2. Correct each string after step 1 into an official organization name.
3. Return a json object mapping from input to the corrected name.

You need to ensure that every output always is an Academy / University / Institute / Laboratory / Company name \
which is a registered full legal name in English, excluding any regional division information and department \
information.

For example:
- You should output "Shanghai Jiao Tong University" for the input "School of Computer Science, Shanghai Jiao Tong \
University" because the input includes information about institutions and departments (schools), but only the \
institution name is needed.
- You should output "The Chinese University of Hong Kong" for the input "The Chinese University of Hong Kong, Shenzhen"\
  because "Shenzhen" is regional division information which is not needed.
- You should output "Huawei Technologies Co., Ltd." instead of "Huawei" or anything else for an input "Huawei Tech. \
  (Shenzhen)", because "Shenzhen" is a regional division information which is not needed and "Huawei Tech." after \
  the removing is not the legal English name of it registered. \
  Similarly, for "Synopsys Inc." and "Synopsys Korea", you should output "Synopsys, Inc." because that is its \
  registered name.


Be carefully that your output **should be a valid json**.

## Correction Guidelines
- Removing department (including schools) info and regional division info before checking if it is the legal English \
  name of the organization registered;
- Return full legal English name of the organization registered;
- Using searching tools if necessary;

## Output Format
Return your answer strictly following JSON object structure

---

## Example

### Input
["University of California, San Diego", "imec", "Ulsan National Institute of Science and Technology (UNIST)"]

### Output
{
    "University of California, San Diego": "University of California",
    "imec": "Interuniversity Microelectronics Centre",
    "Ulsan National Institute of Science and Technology (UNIST)": "Ulsan National Institute of Science and Technology"
}
"""