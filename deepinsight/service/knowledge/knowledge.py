from __future__ import annotations

import os
import shutil
import hashlib
from datetime import datetime
from typing import List, Optional, Tuple

from deepinsight.utils.file_utils import compute_md5
from deepinsight.config.config import Config
from deepinsight.databases.connection import Database
from deepinsight.databases.models.knowledge import KnowledgeBase, KnowledgeDocument
from deepinsight.service.schemas.knowledge import (
    KnowledgeBaseResponse,
    KnowledgeDocumentResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeDocumentCreateRequest,
    ScanAndRegisterRequest,
    FinalizeRequest,
    KnowledgeListRequest,
    KnowledgeDeleteRequest,
    BeginProcessingRequest,
    KnowledgeSearchRequest,
    KnowledgeDocStatus,
)
from deepinsight.service.rag import RAGEngine
from deepinsight.service.schemas.rag import DocumentPayload, Passage
from typing import Optional
from deepinsight.utils.progress import ProgressReporter


class KnowledgeService:
    """
    知识库服务
    - 提供知识库会话创建、文档注册、完成与清理、检索等能力
    - 上层（会议、论文分析等）仅需传入 kb_id 访问解析与检索
    - 所有对外返回统一为 Pydantic Response，避免会话关闭后的 ORM Detached 问题
    """

    def __init__(self, config: Config):
        self._db = Database(config.database)
        self._config = config
        # Initialize RAG engine
        self._rag_engine = RAGEngine(config)

    # ===== 基础能力 =====
    async def create_kb(self, req: KnowledgeBaseCreateRequest) -> KnowledgeBaseResponse:
        with self._db.get_session() as session:  # type: Session
            kb = KnowledgeBase(
                owner_type=req.owner_type,
                owner_id=req.owner_id,
                root_dir=req.root_dir,
                index_dir=req.index_dir,
                parser=req.parser,
                parse_method=req.parse_method,
                embed_model=req.embed_model,
                status="init",
                doc_count=0,
                last_built_at=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(kb)
            session.flush()
            session.refresh(kb)
            # If index_dir is not provided, generate a default path from config and persist
            if not kb.index_dir:
                default_dir = os.path.join(self._config.rag.work_root, "rag_storage", str(kb.kb_id))
                os.makedirs(default_dir, exist_ok=True)
                kb.index_dir = default_dir
                kb.updated_at = datetime.now()
                session.add(kb)
                session.flush()
                session.refresh(kb)
            return KnowledgeBaseResponse.model_validate(kb)

    async def begin_processing(self, req: BeginProcessingRequest) -> KnowledgeBaseResponse:
        with self._db.get_session() as session:
            kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == req.kb_id).first()
            if not kb:
                raise ValueError(f"KnowledgeBase {req.kb_id} not found")
            kb.status = "processing"
            kb.updated_at = datetime.now()
            session.add(kb)
            session.flush()
            session.refresh(kb)
            return KnowledgeBaseResponse.model_validate(kb)

    async def _get_or_create_rag_for_kb(self, session, kb_id: int) -> tuple[KnowledgeBase, str]:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == kb_id).first()
        if not kb:
            raise ValueError(f"KnowledgeBase {kb_id} not found")
        # Ensure index_dir exists
        working_dir = kb.index_dir or os.path.join(self._config.rag.work_root, "rag_storage", str(kb.kb_id))
        os.makedirs(working_dir, exist_ok=True)
        if not kb.index_dir:
            kb.index_dir = working_dir
            kb.updated_at = datetime.now()
            session.add(kb)
            session.flush()
        return kb, working_dir

    async def add_document(self, req: KnowledgeDocumentCreateRequest) -> KnowledgeDocumentResponse:
        with self._db.get_session() as session:
            kb, working_dir = await self._get_or_create_rag_for_kb(session, req.kb_id)

            # Create doc record with minimal writes; flush to obtain id
            doc = KnowledgeDocument(
                kb_id=req.kb_id,
                file_path=req.file_path,
                file_name=req.file_name,
                md5=req.md5,
                parse_status="processing",
                chunks_count=0,
            )
            session.add(doc)
            session.flush()  # assign doc_id without full commit
            session.commit()

            extracted_text: Optional[str] = None
            try:
                payload = DocumentPayload(
                    doc_id=str(doc.doc_id),
                    raw_text="",  # let engine extract from source_path
                    source_path=req.file_path,
                    title=req.file_name or os.path.basename(req.file_path),
                    hash=req.md5,
                    origin="knowledge",
                )
                idx = await self._rag_engine.ingest_document(payload, working_dir)
                doc.parse_status = (
                    idx.process_status.value if hasattr(idx.process_status, "value") else idx.process_status
                ) or doc.parse_status
                if doc.parse_status == "failed" and hasattr(doc, "failed_reason") and not getattr(doc, "failed_reason", None):
                    doc.failed_reason = "LightRAG reported failed"
                doc.chunks_count = idx.chunks_count
                extracted_text = idx.extracted_text
            except Exception as e:
                doc.parse_status = KnowledgeDocStatus.failed.value
                if hasattr(doc, "failed_reason"):
                    doc.failed_reason = str(e)
                raise
            finally:
                # Single commit at end for better performance
                session.commit()
                session.refresh(doc)

            return KnowledgeDocumentResponse(
                doc_id=doc.doc_id,
                kb_id=doc.kb_id,
                file_path=doc.file_path,
                file_name=doc.file_name or os.path.basename(doc.file_path),
                parse_status=doc.parse_status,
                chunks_count=doc.chunks_count,
                extracted_text=extracted_text,
                documents=getattr(idx, "documents", None),
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )

    # ===== 检索能力 =====
    async def search(self, req: KnowledgeSearchRequest) -> List[Passage]:
        """根据 kb_id + query 进行语义检索，返回统一的 Passage 列表。"""
        with self._db.get_session() as session:
            kb, working_dir = await self._get_or_create_rag_for_kb(session, req.kb_id)
            return await self._rag_engine.semantic_search(working_dir, req.query, req.top_k)

    # ===== 完成与失败处理 =====
    async def finalize_success(self, req: FinalizeRequest) -> KnowledgeBaseResponse:
        with self._db.get_session() as session:
            kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == req.kb_id).first()
            if not kb:
                raise ValueError(f"KnowledgeBase {req.kb_id} not found")
            doc_count = session.query(KnowledgeDocument).filter(KnowledgeDocument.kb_id == req.kb_id).count()
            kb.owner_id = req.owner_id if req.owner_id is not None else kb.owner_id
            kb.status = "ready"
            kb.doc_count = doc_count
            kb.last_built_at = datetime.now()
            kb.updated_at = datetime.now()
            session.add(kb)
            session.flush()
            session.refresh(kb)
            return KnowledgeBaseResponse.model_validate(kb)

    async def mark_failed(self, kb_id: int) -> KnowledgeBaseResponse:
        with self._db.get_session() as session:
            kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == kb_id).first()
            if not kb:
                raise ValueError(f"KnowledgeBase {kb_id} not found")
            kb.status = "failed"
            kb.updated_at = datetime.now()
            session.add(kb)
            session.flush()
            session.refresh(kb)
            return KnowledgeBaseResponse.model_validate(kb)

    # ===== 状态恢复能力 =====
    async def restore_state(
        self,
        kb_id: int,
        status: Optional[str] = None,
        doc_count: Optional[int] = None,
        last_built_at: Optional[datetime] = None,
    ) -> KnowledgeBaseResponse:
        with self._db.get_session() as session:
            kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == kb_id).first()
            if not kb:
                raise ValueError(f"KnowledgeBase {kb_id} not found")
            if status is not None:
                kb.status = status
            if doc_count is not None:
                kb.doc_count = doc_count
            if last_built_at is not None:
                kb.last_built_at = last_built_at
            kb.updated_at = datetime.now()
            session.add(kb)
            session.flush()
            session.refresh(kb)
            return KnowledgeBaseResponse.model_validate(kb)

    async def cleanup_kb(self, kb_id: int) -> bool:
        working_dir = None
        root_dir = None
        with self._db.get_session() as session:
            kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == kb_id).first()
            if kb:
                working_dir = kb.index_dir or os.path.join(self._config.rag.work_root, "rag_storage", str(kb_id))
                root_dir = kb.root_dir
            session.query(KnowledgeDocument).filter(KnowledgeDocument.kb_id == kb_id).delete()
            affected = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == kb_id).delete()
            session.commit()
        # 删除 RAG 工作目录
        if working_dir and os.path.isdir(working_dir):
            try:
                shutil.rmtree(working_dir)
            except Exception:
                pass
        # 删除原始文档根目录（origin files）
        if root_dir and os.path.isdir(root_dir):
            try:
                shutil.rmtree(root_dir)
            except Exception:
                pass
        return affected > 0

    # ===== 查询与删除 =====
    async def list_kbs(self, req: KnowledgeListRequest) -> List[KnowledgeBaseResponse]:
        with self._db.get_session() as session:
            q = session.query(KnowledgeBase)
            if req.owner_type:
                q = q.filter(KnowledgeBase.owner_type == req.owner_type)
            if req.owner_id is not None:
                q = q.filter(KnowledgeBase.owner_id == req.owner_id)
            if req.status:
                q = q.filter(KnowledgeBase.status == req.status)
            items = q.offset(req.offset).limit(req.limit).all()
            return [KnowledgeBaseResponse.model_validate(i) for i in items]

    async def delete_kb(self, req: KnowledgeDeleteRequest) -> bool:
        return await self.cleanup_kb(req.kb_id)

    async def retry_unfinished_docs(self, kb_id: int, reporter: Optional[ProgressReporter] = None) -> List[KnowledgeDocumentResponse]:
        with self._db.get_session() as session:
            from deepinsight.databases.models.knowledge import KnowledgeDocument
            docs = (
                session.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.kb_id == kb_id,
                    KnowledgeDocument.parse_status.in_(["failed", "pending", "processing"]),
                )
                .all()
            )
            items: List[KnowledgeDocumentResponse] = []
            total = len(docs)
            if reporter is not None and total > 0:
                reporter.begin(total=total, description="Listing unfinished documents")
            for doc in docs:
                resp = KnowledgeDocumentResponse(
                    doc_id=doc.doc_id,
                    kb_id=doc.kb_id,
                    file_path=doc.file_path,
                    file_name=doc.file_name or os.path.basename(doc.file_path),
                    parse_status=KnowledgeDocStatus(doc.parse_status),
                    chunks_count=doc.chunks_count,
                    extracted_text=None,
                    documents=None,
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                )
                items.append(resp)
                if reporter is not None:
                    reporter.advance(step=1, detail=os.path.basename(doc.file_path))
            if reporter is not None and total > 0:
                reporter.complete()
            return items

    async def reparse_document(self, kb_id: int, doc_id: int) -> KnowledgeDocumentResponse:
        with self._db.get_session() as session:
            kb, working_dir = await self._get_or_create_rag_for_kb(session, kb_id)
            doc = (
                session.query(KnowledgeDocument)
                .filter(KnowledgeDocument.kb_id == kb_id, KnowledgeDocument.doc_id == doc_id)
                .first()
            )
            if not doc:
                raise ValueError("Document not found")
            doc.parse_status = KnowledgeDocStatus.processing.value
            session.add(doc)
            session.flush()
            extracted_text: Optional[str] = None
            idx = None
            try:
                payload = DocumentPayload(
                    doc_id=str(doc.doc_id),
                    raw_text="",
                    source_path=doc.file_path,
                    title=doc.file_name or os.path.basename(doc.file_path),
                    hash=doc.md5,
                    origin="knowledge_retry",
                )
                idx = await self._rag_engine.ingest_document(payload, working_dir)
                doc.parse_status = (
                    idx.process_status.value if hasattr(idx.process_status, "value") else idx.process_status
                ) or doc.parse_status
                if doc.parse_status == KnowledgeDocStatus.failed.value and not getattr(doc, "failed_reason", None):
                    doc.failed_reason = "Retry failed"
                doc.chunks_count = idx.chunks_count
                extracted_text = idx.extracted_text
                session.commit()
                session.refresh(doc)
            except Exception as e:
                doc.parse_status = KnowledgeDocStatus.failed.value
                if hasattr(doc, "failed_reason"):
                    doc.failed_reason = str(e)
                session.commit()
                raise
            return KnowledgeDocumentResponse(
                doc_id=doc.doc_id,
                kb_id=doc.kb_id,
                file_path=doc.file_path,
                file_name=doc.file_name or os.path.basename(doc.file_path),
                parse_status=KnowledgeDocStatus(doc.parse_status),
                chunks_count=doc.chunks_count,
                extracted_text=extracted_text,
                documents=getattr(idx, "documents", None),
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )
        