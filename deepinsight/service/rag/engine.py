from __future__ import annotations

import os
from typing import Dict, List, Optional, Any

from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc
from lightrag.llm.hf import hf_embed
from transformers import AutoModel, AutoTokenizer
from lightrag.kg.shared_storage import initialize_pipeline_status

from deepinsight.config.config import CONFIG, Config
from deepinsight.utils.llm_utils import init_lightrag_llm_model_func

from ..schemas.rag import DocumentPayload, IndexResult, Passage, DocProcessStatus


class RAGEngine:
    """Async local engine wrapper around LightRAG.

    Key points:
    - Cache LightRAG instances by `working_dir` to avoid re-initialization;
    - Use `ainsert` for async document parsing and ingestion (await until done);
    - Provide unified async semantic search returning standardized `Passage` list;
    - Align initialization and embedding behavior with KnowledgeService.
    """

    def __init__(self, config: Optional[Config] = None):
        self._rag_cache: Dict[str, LightRAG] = {}
        self._rag_initialized: set[str] = set()
        self._llm_func = None
        self._config = config or CONFIG

    async def _get_or_create_rag(self, working_dir: str) -> LightRAG:
        if not working_dir:
            raise ValueError("working_dir must not be empty")
        os.makedirs(working_dir, exist_ok=True)

        rag = self._rag_cache.get(working_dir)
        if rag is None:
            if self._llm_func is None:
                if self._config is None:
                    raise RuntimeError("Config not initialized; load config before using RAG engine")
                self._llm_func = init_lightrag_llm_model_func(self._config)

            rag = LightRAG(
                working_dir=working_dir,
                llm_model_func=self._llm_func,
                embedding_func=EmbeddingFunc(
                    embedding_dim=384,
                    func=lambda texts: hf_embed(
                        texts,
                        tokenizer=AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2"),
                        embed_model=AutoModel.from_pretrained("sentence-transformers/all-MiniLM-L6-v2"),
                    ),
                ),
            )
            self._rag_cache[working_dir] = rag

        if working_dir not in self._rag_initialized:
            await rag.initialize_storages()
            await initialize_pipeline_status()
            self._rag_initialized.add(working_dir)
        return rag

    async def ingest_document(self, doc: DocumentPayload, working_dir: str) -> IndexResult:
        """Parse and index a document (fully async).

        Behavior:
        - If `raw_text` is provided, ingest directly;
        - Else read from `source_path` using LangChain document loaders;
        - Return standardized `IndexResult` including parsed documents list.
        """
        rag = await self._get_or_create_rag(working_dir)

        documents_data: List[dict] = []
        if doc.raw_text and doc.raw_text.strip():
            text = doc.raw_text
            file_paths = [doc.source_path] if doc.source_path else None
            # Build a single LangChain-like document for downstream usage
            try:
                from langchain_core.documents import Document as LCDocument
                lc_doc = LCDocument(page_content=text, metadata={"source": doc.source_path or "inline"})
                documents_data = [{"page_content": lc_doc.page_content, "metadata": lc_doc.metadata}]
            except Exception:
                documents_data = [{"page_content": text, "metadata": {"source": doc.source_path or "inline"}}]
            # Pass relational DB document id into LightRAG for linkage
            await rag.ainsert([text], ids=[doc.doc_id], file_paths=file_paths)
            chunks_count = _estimate_chunks(text)
            process_status = await self._fetch_doc_status(rag, str(doc.doc_id))
        else:
            if not doc.source_path or not os.path.isfile(doc.source_path):
                raise ValueError("raw_text not provided and source_path missing or unreadable")
            # Use LangChain document loaders to parse various file types
            docs = _load_langchain_documents(doc.source_path)
            documents_data = [{"page_content": d.page_content, "metadata": getattr(d, "metadata", {})} for d in docs]
            text = "\n\n".join(d["page_content"] for d in documents_data if d.get("page_content"))
            # Pass relational DB document id into LightRAG for linkage
            await rag.ainsert([text], ids=[doc.doc_id], file_paths=[doc.source_path])
            chunks_count = _estimate_chunks(text)
            process_status = await self._fetch_doc_status(rag, str(doc.doc_id))

        return IndexResult(
            doc_id=doc.doc_id,
            indexed=True,
            chunks_count=chunks_count,
            extracted_text=text,
            documents=documents_data,
            process_status=process_status,
        )

    async def semantic_search(self, working_dir: str, query: str, top_k: int = 8) -> List[Passage]:
        """Unified async semantic search.

        To be compatible with different LightRAG versions, we adapt method names:
        - Prefer `asearch`, fallback to `aquery`, finally try sync `search` in a thread.
        Results are best-effort mapped to `Passage`.
        """
        rag = await self._get_or_create_rag(working_dir)

        # Call appropriate search method based on availability
        result: Any
        if hasattr(rag, "asearch"):
            result = await rag.asearch(query, top_k=top_k)
        elif hasattr(rag, "aquery"):
            result = await rag.aquery(query, top_k=top_k)
        elif hasattr(rag, "search"):
            import asyncio
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: rag.search(query, top_k=top_k))
        else:
            result = []

        passages: List[Passage] = []
        # Normalize result forms: list[str] / list[dict] / custom objects
        base_doc_id = f"kb:{os.path.basename(working_dir) or 'default'}"
        if isinstance(result, list):
            for i, item in enumerate(result):
                if isinstance(item, str):
                    passages.append(Passage(doc_id=base_doc_id, chunk_id=f"res:{i}", text=item, score=0.0, meta={}))
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or str(item)
                    score = float(item.get("score", 0.0))
                    meta = {k: v for k, v in item.items() if k not in {"text", "content", "score"}}
                    passages.append(Passage(doc_id=base_doc_id, chunk_id=f"res:{i}", text=text, score=score, meta=meta))
                else:
                    txt = getattr(item, "text", None) or getattr(item, "content", None) or str(item)
                    scor = float(getattr(item, "score", 0.0))
                    passages.append(Passage(doc_id=base_doc_id, chunk_id=f"res:{i}", text=txt, score=scor, meta={}))
        return passages[:top_k]

    async def _fetch_doc_status(self, rag: LightRAG, doc_id: str) -> Optional[DocProcessStatus]:
        try:
            res = await rag.aget_docs_by_ids([doc_id])

            if isinstance(res, dict):
                item = res.get(doc_id) or next(iter(res.values()), None)
            elif isinstance(res, list):
                item = res[0] if res else None
            else:
                item = res

            if item is None:
                return DocProcessStatus.failed

            status = item["status"]
            if not status:
                return DocProcessStatus.failed

            s = None
            if isinstance(status, str):
                s = status.lower()
            else:
                s = getattr(status, "value", None)
                if isinstance(s, str):
                    s = s.lower()
                else:
                    # Fallback to string repr
                    s = str(status).lower()

            if s in {"pending", "processing", "preprocessed"}:
                return DocProcessStatus.processing
            if s in {"processed"}:
                return DocProcessStatus.parsed
            if s in {"failed"}:
                return DocProcessStatus.failed
            return DocProcessStatus.failed
        except Exception:
            return DocProcessStatus.failed


# --------- Robust text IO helpers (aligned with KnowledgeService behavior) ---------

def _load_langchain_documents(file_path: str):
    """Load documents via LangChain loaders for various file types.
    Returns a list of LangChain `Document` objects; falls back to a single Document with extracted text.
    """
    ext = os.path.splitext(file_path.lower())[1]
    docs: List[Any] = []
    try:
        if ext == ".pdf":
            try:
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(file_path)
                docs = loader.load()
            except Exception:
                docs = []
        elif ext in {".txt", ".md", ".markdown"}:
            try:
                from langchain_community.document_loaders import TextLoader
                loader = TextLoader(file_path, encoding="utf-8")
                docs = loader.load()
            except Exception:
                docs = []
        elif ext == ".csv":
            try:
                from langchain_community.document_loaders import CSVLoader
                loader = CSVLoader(file_path)
                docs = loader.load()
            except Exception:
                docs = []
        elif ext == ".docx":
            try:
                from langchain_community.document_loaders import Docx2txtLoader
                loader = Docx2txtLoader(file_path)
                docs = loader.load()
            except Exception:
                docs = []
        else:
            # Default: try text loader
            try:
                from langchain_community.document_loaders import TextLoader
                loader = TextLoader(file_path, encoding="utf-8")
                docs = loader.load()
            except Exception:
                docs = []
    except Exception:
        docs = []

    if not docs:
        # Fallback to legacy text extraction and wrap into a single Document
        text = _extract_text(file_path)
        try:
            from langchain_core.documents import Document as LCDocument
            return [LCDocument(page_content=text, metadata={"source": file_path})]
        except Exception:
            # Minimal object with similar attributes to LangChain Document
            class _Doc:
                def __init__(self, content, metadata):
                    self.page_content = content
                    self.metadata = metadata
            return [_Doc(text, {"source": file_path})]

    return docs


def _extract_text(file_path: str) -> str:
    _, ext = os.path.splitext(file_path.lower())
    text_based_exts = {".txt", ".md", ".markdown"}
    try:
        if ext in text_based_exts:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        else:
            try:
                import textract  # lazy import to avoid hard dependency
                content = textract.process(file_path)
                return content.decode("utf-8", errors="ignore")
            except Exception:
                with open(file_path, "rb") as f:
                    raw = f.read()
                try:
                    return raw.decode("utf-8")
                except Exception:
                    return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"Text extraction failed for {file_path}: {e}")


def _estimate_chunks(text: str) -> int:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return max(1, len(paragraphs))