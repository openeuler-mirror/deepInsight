# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from __future__ import annotations

import os
import shutil
import logging
from datetime import datetime
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, ConfigDict, ValidationError, AnyHttpUrl

from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from deepinsight.utils.file_utils import compute_md5
from deepinsight.databases.models.academic import Conference, Paper, PaperAuthorRelation, Author
from deepinsight.databases.models.knowledge import KnowledgeBase
from deepinsight.databases.connection import Database
from deepinsight.config.config import Config
from deepinsight.service.knowledge.knowledge import KnowledgeService
from deepinsight.service.schemas.knowledge import (
    KnowledgeBaseCreateRequest,
    KnowledgeDocumentCreateRequest,
    KnowledgeListRequest,
    BeginProcessingRequest,
    ScanAndRegisterRequest,
    FinalizeRequest,
    KnowledgeBaseResponse,
)
from deepinsight.service.schemas.common import OwnerType
from deepinsight.service.schemas.conference import (
    ConferenceCreateRequest,
    ConferenceListRequest,
    ConferenceUpdateRequest,
    ConferenceDeleteRequest,
    ConferenceResponse,
    ConferenceListResponse,
    DeleteConferenceResponse,
    GenerateKnowledgeBaseRequest,
    ConferenceParseDocsRequest,
)
from deepinsight.utils.progress import ProgressReporter
from deepinsight.utils.llm_utils import init_langchain_models_from_llm_config
from deepinsight.service.conference.paper_extractor import PaperExtractionService
from deepinsight.service.schemas.paper_extract import ExtractPaperMetaRequest, ExtractPaperMetaFromDocsRequest, DocSegment
from deepinsight.core.agent.conference_research.conf_topic import get_conference_topics

class ConferenceService:
    """
    顶会管理服务（会议信息）
    - 创建/更新/删除/查询 Conference 记录
    - 入参统一封装为 Pydantic schemas，便于 API 与 CLI 复用
    - 返回统一的 Response schemas，避免直接暴露 ORM 模型
    """

    def __init__(self, config: Config):
        # 使用 Config.database 直接初始化 Database（DatabaseConfig）
        self._db = Database(config.database)
        self._config = config
        self._knowledge = KnowledgeService(config)
        # 初始化论文提取服务
        self._paper_extractor = PaperExtractionService(config)

    async def create_conference(self, data: ConferenceCreateRequest) -> ConferenceResponse:
        # Optionally enrich metadata via web search when short_name+year provided
        # Only query when topics or website are missing to avoid unnecessary calls
        if data.short_name and data.year and (not data.topics or not data.website):
            try:
                meta = await self._query_conference_meta(data.short_name, data.year)
                # Fill missing fields with queried values
                if not data.full_name and meta.full_name:
                    data.full_name = meta.full_name
                if not data.website and meta.website:
                    # Convert AnyHttpUrl to plain string for DB compatibility
                    data.website = str(meta.website)
                if not data.topics and meta.topics:
                    data.topics = meta.topics
            except self.ConferenceQueryException as e:
                # Surface query-related errors (e.g., missing API key) to client
                raise e
            except Exception:
                # Best-effort enrichment; continue creation if query fails
                raise
        with self._db.get_session() as db:  # type: Session
            conf = Conference(
                full_name=data.full_name,
                short_name=data.short_name,
                year=data.year,
                location=data.location,
                start_date=data.start_date,
                end_date=data.end_date,
                website=data.website,
                topics=data.topics,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db.add(conf)
            db.commit()
            db.refresh(conf)
            return ConferenceResponse.model_validate(conf)

    # --- Conference Metadata Query (moved from test2.py) ---
    class _Conf(BaseModel):
        model_config = ConfigDict(extra="forbid")

        full_name: str
        """Conference's official full name in its native language."""
        website: Annotated[str, AnyHttpUrl] | None
        """Conference's official website HTTP/HTTPS URL. Maybe empty."""


    class Conference(_Conf):
        topics: list[str] = Field(default_factory=list, min_length=0)

    class _ConfWithErr(_Conf):
        error: Annotated[str | None, Field(exclude=True)] = None

    class ConferenceQueryException(RuntimeError):
        """A mark meaning the error message can pass out to client."""

    _QUERY_METADATA_SYSTEM_PROMPT = """## Role
You are an Academic Information Retrieval Expert and Data Formatting Engineer.  
Your task is to search the given conference online and extract structured result in a strict JSON format.

## Task
1. Search the given conference information online by searching tool.
2. Extract the following metadata fields of the given conference from the search result and your knowledge:
   1. Official full name (in original language provided by the conference organizer/website without translation);
   2. Official website http/https URL (if found, else leave it be null);
3. If tool call fail, output an error message about the reason via "error" (but you still need to output an empty \
string as "full_name"). In all other cases, "full_name" must not be empty, and you do not need to output "error."

## Output Format
Return your answer strictly following this JSON structure:

{
    "full_name": "",
    "website": "",
    "error": ""
}

---

## Example

### Input
Give me the information about OSP in 2025.

### Search Tool Returns
[
    {
        "content": "OSP takes a broad view of systems and solicits contributions from many fields including: \
operating systems, file and storage systems, and troubleshooting of complex systems. We also welcome work that \
explores the interaction of computer systems with related areas such as computer architecture and databases."
    },
    {
        "content": "OSP(2025) website: https://example.com/2025/index.html"
    },
    {
        "source": "https://example.com/2025/index.html",
        "content": "OSP 2025\\nThe 3rd Operating Systems Principles\\n...."
    }
]

### Final Output (no "error" because everything is OK)
{
    "full_name": "The 3rd Operating Systems Principles",
    "website": "https://example.com/2025/index.html"
}
"""

    async def _query_conference_meta(self, short_name: str, year: int):
        # Initialize LLM
        _, llm = init_langchain_models_from_llm_config(self._config.llms)
        
        # Check Tavily API key before attempting online search
        if not os.environ.get("TAVILY_API_KEY"):
            raise self.ConferenceQueryException(
                "Environment variable `TAVILY_API_KEY` not detected. Please configure it and try again: for example, run `export TAVILY_API_KEY=<your_key>` in your shell or set it in the project's `.env` file."
            )
        
        # Initialize search tool (best-effort)
        tools = []
        try:
            from langchain_tavily import TavilySearch
            tools = [TavilySearch()]
        except Exception:
            pass
        
        agent = create_agent(
            model=llm, 
            tools=tools, 
            system_prompt=self._QUERY_METADATA_SYSTEM_PROMPT,
            response_format=ToolStrategy(self._ConfWithErr),
        )

        base_meta = self._Conf(full_name=short_name, website=None)
        user_query = f"Give me the information about {short_name} in {year}."
        try:
            # Prefer the agent's native async invocation contract
            result = await agent.with_retry().ainvoke(
                input=dict(
                    messages=[
                        HumanMessage(content=user_query)
                    ]
                ),
            )
            result = result["structured_response"]
            if result.error:
                logging.error(f"Search conference info failed: {result.error}")
                raise self.ConferenceQueryException("Search conference info failed")
        except Exception as err:
            logging.error(f"Search conference info failed: {err}")
            raise self.ConferenceQueryException(str(err))
        base_meta = result
        user_query = f"Give me the topics of {short_name} in {year}."
        topics = []
        try:
            topics = await get_conference_topics(user_query, llm)
        except Exception as err:
            logging.error(f"Get conference topics failed: {err}")
            raise
        metadata = self.Conference(full_name=base_meta.full_name, website=base_meta.website, topics=topics)
        return metadata

    async def list_conferences(self, query: ConferenceListRequest) -> ConferenceListResponse:
        with self._db.get_session() as db:  # type: Session
            q = db.query(Conference)
            if query.short_name:
                q = q.filter(Conference.short_name == query.short_name)
            if query.year:
                q = q.filter(Conference.year == query.year)
            if query.location:
                q = q.filter(Conference.location == query.location)
            items = q.offset(query.offset).limit(query.limit).all()
            return ConferenceListResponse(
                items=[ConferenceResponse.model_validate(c) for c in items],
                count=len(items),
            )


    async def update_conference(self, data: ConferenceUpdateRequest) -> Optional[ConferenceResponse]:
        with self._db.get_session() as db:  # type: Session
            conf = db.query(Conference).filter(Conference.conference_id == data.conference_id).first()
            if not conf:
                return None

            update_fields = data.model_dump(exclude={"conference_id"}, exclude_none=True)
            for k, v in update_fields.items():
                setattr(conf, k, v)
            conf.updated_at = datetime.now()

            db.commit()
            db.refresh(conf)
            return ConferenceResponse.model_validate(conf)


    async def delete_conference(self, data: ConferenceDeleteRequest) -> DeleteConferenceResponse:
        with self._db.get_session() as db:  # type: Session
            conf = db.query(Conference).filter(Conference.conference_id == data.conference_id).first()
            if not conf:
                return DeleteConferenceResponse(ok=False)
            # 先清理关联的知识库，包括删除 LightRAG 目录
            kbs = await self._knowledge.list_kbs(
                KnowledgeListRequest(owner_type=OwnerType.CONFERENCE, owner_id=conf.conference_id)
            )
            for kb in kbs:
                try:
                    await self._knowledge.cleanup_kb(kb.kb_id)
                except Exception:
                    # 忽略单个知识库清理失败，继续删除会议记录
                    pass
            # 清理该会议下的论文及作者关系
            self._cleanup_academic_by_conference(db, conf.conference_id)
            # 默认清理孤儿作者（不被任何论文引用的作者）
            self._cleanup_orphan_authors(db)
            db.delete(conf)
            db.commit()
            return DeleteConferenceResponse(ok=True)
        
    async def generate_kb_for_conference(self, req: GenerateKnowledgeBaseRequest) -> KnowledgeBaseResponse:
        """为指定会议生成知识库，编排创建->扫描->完成"""
        # 1) 校验会议存在
        with self._db.get_session() as db:
            conf = db.query(Conference).filter(Conference.conference_id == req.conference_id).first()
            if not conf:
                raise ValueError(f"Conference {req.conference_id} not found")
        # 2) 创建知识库占位
        kb = await self._knowledge.create_kb(
            KnowledgeBaseCreateRequest(
                owner_type=OwnerType.CONFERENCE,
                owner_id=None,
                root_dir=req.docs_root_dir,
                index_dir=None,
                parser=req.parser,
                parse_method=req.parse_method,
                embed_model=req.embed_model,
            )
        )
        try:
            # 3) 置为 processing
            await self._knowledge.begin_processing(BeginProcessingRequest(kb_id=kb.kb_id))
            # 4) 扫描目录并注册文档（覆盖常见格式）
            await self.scan_dir_and_register_docs(
                ScanAndRegisterRequest(
                    kb_id=kb.kb_id,
                    root_dir=req.docs_root_dir,
                    exts=(".pdf", ".md", ".txt", ".doc", ".docx", ".ppt", ".pptx"),
                    conference_id=req.conference_id,
                )
            )
            # 5) 完成并绑定会议ID
            final = await self._knowledge.finalize_success(
                FinalizeRequest(kb_id=kb.kb_id, owner_id=req.conference_id)
            )
            return final
        except Exception:
            await self._knowledge.mark_failed(kb.kb_id)
            raise
        
    async def ensure_conference_and_ingest_docs(self, req: ConferenceParseDocsRequest, reporter: Optional[ProgressReporter] = None) -> None:
        """Ensure conference exists and ingest documents.
        - If conference not exists: create conference, copy folder, register docs.
        - If exists: diff new folder vs existing KB root_dir and ingest incrementally.
        - Performs rollback on failure; no return value.
        """
        # Ensure src dir exists
        if not os.path.isdir(req.docs_src_dir):
            raise ValueError(f"docs_src_dir not found: {req.docs_src_dir}")

        # Resolve or create conference id
        conf_id = await self._resolve_conference_id(req)

        # Locate existing KB for the conference
        existing_kbs = await self._knowledge.list_kbs(KnowledgeListRequest(owner_type=OwnerType.CONFERENCE, owner_id=conf_id))
        kb = existing_kbs[0] if existing_kbs else None

        if kb is None:
            # Initial ingestion path
            await self._initial_ingest_for_conference(conf_id, req, reporter)
            return

        # Before incremental ingestion: retry unfinished docs if any
        if kb is not None:
            await self._reparse_unfinished_docs_for_conference(kb.kb_id, conf_id, reporter)

        # Incremental ingestion path
        await self._incremental_ingest_for_conference(kb, conf_id, req, reporter)
        return

    async def _reparse_unfinished_docs_for_conference(self, kb_id: int, conference_id: int, reporter: Optional[ProgressReporter]) -> None:
        try:
            docs = await self._knowledge.retry_unfinished_docs(kb_id, reporter=reporter)
            if docs:
                if reporter is not None:
                    reporter.begin(total=len(docs), description="Reparsing unfinished documents")
                for d in docs:
                    doc_resp = await self._knowledge.reparse_document(kb_id, d.doc_id)
                    try:
                        if getattr(doc_resp, "documents", None):
                            await self._paper_extractor.extract_and_store_from_documents(
                                ExtractPaperMetaFromDocsRequest(
                                    conference_id=conference_id,
                                    filename=doc_resp.file_name,
                                    documents=[DocSegment(content=dd.get("page_content", ""), metadata=dd.get("metadata", {})) for dd in (doc_resp.documents or [])],
                                )
                            )
                        elif doc_resp.extracted_text:
                            await self._paper_extractor.extract_and_store(
                                ExtractPaperMetaRequest(
                                    conference_id=conference_id,
                                    filename=doc_resp.file_name,
                                    paper=doc_resp.extracted_text,
                                )
                            )
                    except Exception:
                        logging.exception("Paper metadata extraction failed for %s", doc_resp.file_name)
                    if reporter is not None:
                        reporter.advance(step=1, detail=doc_resp.file_name)
                if reporter is not None:
                    reporter.complete()
        except Exception:
            logging.exception("Retry unfinished documents failed; continue with incremental ingestion")

    def _list_files(self, base: str, exts: tuple[str, ...]) -> list[str]:
        files: list[str] = []
        for dp, _, fns in os.walk(base):
            for fn in fns:
                if exts and not any(fn.lower().endswith(ext) for ext in exts):
                    continue
                files.append(os.path.join(dp, fn))
        return files

    async def _resolve_conference_id(self, req: ConferenceParseDocsRequest) -> int:
        with self._db.get_session() as db:
            conf = None
            if req.short_name and req.year:
                conf = db.query(Conference).filter(Conference.short_name == req.short_name, Conference.year == req.year).first()
            if not conf and req.full_name and req.year:
                conf = db.query(Conference).filter(Conference.full_name == req.full_name, Conference.year == req.year).first()
            conf_id = conf.conference_id if conf else None
        if conf_id is None:
            if not req.full_name or not req.year:
                raise ValueError("Creating conference requires full_name and year when not exists")
            created = await self.create_conference(
                ConferenceCreateRequest(
                    full_name=req.full_name,
                    short_name=req.short_name,
                    year=req.year,
                    location=req.location,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    website=req.website,
                    topics=req.topics,
                )
            )
            conf_id = created.conference_id
        return conf_id

    async def _initial_ingest_for_conference(self, conf_id: int, req: ConferenceParseDocsRequest, reporter: Optional[ProgressReporter]) -> None:
        target_root = os.path.join(self._config.rag.work_root, "original_files", "conference", str(conf_id))
        os.makedirs(target_root, exist_ok=True)
        dest_dir = target_root
        shutil.copytree(req.docs_src_dir, dest_dir, dirs_exist_ok=True)
        kb = await self._knowledge.create_kb(
            KnowledgeBaseCreateRequest(
                owner_type=OwnerType.CONFERENCE,
                owner_id=conf_id,
                root_dir=dest_dir,
                index_dir=None,
                parser=req.parser,
                parse_method=req.parse_method,
                embed_model=req.embed_model,
            )
        )
        try:
            await self._knowledge.begin_processing(BeginProcessingRequest(kb_id=kb.kb_id))
            count = await self.scan_dir_and_register_docs(
                ScanAndRegisterRequest(kb_id=kb.kb_id, root_dir=dest_dir, exts=tuple(req.exts), conference_id=conf_id),
                reporter=reporter,
            )
            if count == 0:
                await self._knowledge.cleanup_kb(kb.kb_id)
                with self._db.get_session() as db:
                    c = db.query(Conference).filter(Conference.conference_id == conf_id).first()
                    if c:
                        db.delete(c)
                        db.commit()
                raise ValueError("No documents ingested")
            await self._knowledge.finalize_success(FinalizeRequest(kb_id=kb.kb_id, owner_id=conf_id))
        except Exception:
            await self._knowledge.mark_failed(kb.kb_id)
            await self._knowledge.cleanup_kb(kb.kb_id)
            with self._db.get_session() as db:
                c = db.query(Conference).filter(Conference.conference_id == conf_id).first()
                if c:
                    db.delete(c)
                    db.commit()
            raise

    async def _incremental_ingest_for_conference(self, kb: KnowledgeBaseResponse, conf_id: int, req: ConferenceParseDocsRequest, reporter: Optional[ProgressReporter]) -> None:
        existing_root = kb.root_dir
        old_files = {os.path.basename(p) for p in self._list_files(existing_root, tuple(req.exts))}
        new_files_paths = self._list_files(req.docs_src_dir, tuple(req.exts))
        existing_md5s: set[str] = set()
        with self._db.get_session() as db:
            from deepinsight.databases.models.knowledge import KnowledgeDocument
            md5_rows = db.query(KnowledgeDocument.md5).filter(
                KnowledgeDocument.kb_id == kb.kb_id,
                KnowledgeDocument.md5.isnot(None)
            ).all()
            existing_md5s = {row[0] for row in md5_rows if row[0] is not None}
        if existing_md5s:
            add_paths = [p for p in new_files_paths if compute_md5(p) not in existing_md5s]
        else:
            add_paths = [p for p in new_files_paths if os.path.basename(p) not in old_files]
        if not add_paths:
            return
        for src in add_paths:
            name = os.path.basename(src)
            dst = os.path.join(existing_root, name)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
        original_status = kb.status
        original_doc_count = kb.doc_count
        original_last_built_at = kb.last_built_at
        try:
            await self._knowledge.begin_processing(BeginProcessingRequest(kb_id=kb.kb_id))
            if reporter is not None:
                reporter.begin(total=len(add_paths), description="Registering new documents")
            for src in add_paths:
                name = os.path.basename(src)
                dst = os.path.join(existing_root, name)
                doc_resp = await self._knowledge.add_document(
                    KnowledgeDocumentCreateRequest(
                        kb_id=kb.kb_id,
                        file_path=dst,
                        file_name=name,
                        md5=compute_md5(dst),
                    )
                )
                if conf_id is not None:
                    try:
                        if getattr(doc_resp, "documents", None):
                            await self._paper_extractor.extract_and_store_from_documents(
                                ExtractPaperMetaFromDocsRequest(
                                    conference_id=conf_id,
                                    filename=name,
                                    documents=[DocSegment(content=d.get("page_content", ""), metadata=d.get("metadata", {})) for d in (doc_resp.documents or [])],
                                )
                            )
                        elif doc_resp.extracted_text:
                            await self._paper_extractor.extract_and_store(
                                ExtractPaperMetaRequest(
                                    conference_id=conf_id,
                                    filename=name,
                                    paper=doc_resp.extracted_text,
                                )
                            )
                    except Exception as e:
                        logging.warning(f"Paper extraction failed for {dst}: {e}")
                if reporter is not None:
                    reporter.advance(step=1)
            await self._knowledge.finalize_success(FinalizeRequest(kb_id=kb.kb_id, owner_id=conf_id))
            if reporter is not None:
                reporter.complete()
        except Exception:
            for src in add_paths:
                name = os.path.basename(src)
                dst = os.path.join(existing_root, name)
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except Exception:
                        pass
            await self._knowledge.restore_state(
                kb_id=kb.kb_id,
                status=original_status,
                doc_count=original_doc_count,
                last_built_at=original_last_built_at,
            )
            raise

    async def scan_dir_and_register_docs(self, req: ScanAndRegisterRequest, reporter: Optional[ProgressReporter] = None) -> int:
        count = 0
        # Determine base directory
        base_dir: Optional[str] = req.root_dir
        if not base_dir:
            with self._db.get_session() as db:
                kb = db.query(KnowledgeBase).filter(KnowledgeBase.kb_id == req.kb_id).first()
                if not kb:
                    raise ValueError(f"KnowledgeBase {req.kb_id} not found")
                base_dir = kb.root_dir
        if not base_dir or not os.path.isdir(base_dir):
            raise ValueError(f"root_dir not found: {base_dir}")
        exts = req.exts
        
        # 使用外部传入的会议ID，用于后续论文提取
        conference_id = req.conference_id

        files_to_process: list[tuple[str, str]] = []
        for dirpath, _, filenames in os.walk(base_dir):
            for fname in filenames:
                if exts and not any(fname.lower().endswith(ext) for ext in exts):
                    continue
                fpath = os.path.join(dirpath, fname)
                files_to_process.append((fpath, fname))
        if reporter is not None:
            reporter.begin(total=len(files_to_process), description="Registering documents")
        for fpath, fname in files_to_process:
            try:
                doc_resp = await self._knowledge.add_document(
                    KnowledgeDocumentCreateRequest(
                        kb_id=req.kb_id,
                        file_path=fpath,
                        file_name=fname,
                        md5=compute_md5(fpath),
                    )
                )
                # 在文档解析完成后，进行论文元数据提取
                if conference_id is not None:
                    try:
                        if getattr(doc_resp, "documents", None):
                            await self._paper_extractor.extract_and_store_from_documents(
                                ExtractPaperMetaFromDocsRequest(
                                    conference_id=conference_id,
                                    filename=fname,
                                    documents=[DocSegment(content=d.get("page_content", ""), metadata=d.get("metadata", {})) for d in (doc_resp.documents or [])],
                                )
                            )
                        elif doc_resp.extracted_text:
                            await self._paper_extractor.extract_and_store(
                                ExtractPaperMetaRequest(
                                    conference_id=conference_id,
                                    filename=fname,
                                    paper=doc_resp.extracted_text,
                                )
                            )
                    except Exception as e:
                        # 论文提取失败不影响整体注册流程，记录日志并继续
                        logging.warning(f"Paper extraction failed for {fpath}: {e}")
                count += 1
                if reporter is not None:
                    reporter.advance(step=1)
            except Exception as e:
                if reporter is not None:
                    reporter.fail(detail=fpath, error=e)
                raise RuntimeError(f"scan_dir_and_register_docs failed on file {fpath}: {e}") from e
        if reporter is not None:
            reporter.complete()
        return count
        
    def _cleanup_academic_by_conference(self, db, conf_id: int) -> None:
        """删除会议下的论文及其作者关系。作者本身不删除。"""
        # 找出会议下所有论文ID
        paper_ids = [pid for (pid,) in db.query(Paper.paper_id).filter(Paper.conference_id == conf_id).all()]
        if not paper_ids:
            return
        # 先删除作者关系，再删除论文
        db.query(PaperAuthorRelation).filter(PaperAuthorRelation.paper_id.in_(paper_ids)).delete(synchronize_session=False)
        db.query(Paper).filter(Paper.paper_id.in_(paper_ids)).delete(synchronize_session=False)
        db.commit()
        
    def _cleanup_orphan_authors(self, db) -> None:
        """可选：删除不被任何论文引用的作者（默认不调用）。"""
        # 使用 NOT EXISTS 避免 SQLAlchemy 关于 IN 子查询的警告，并提升兼容性
        exists_rel = db.query(PaperAuthorRelation).filter(
            PaperAuthorRelation.author_id == Author.author_id
        ).exists()
        db.query(Author).filter(~exists_rel).delete(synchronize_session=False)
        db.commit()
        