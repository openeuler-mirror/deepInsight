from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from lightrag import LightRAG, QueryParam
from lightrag.llm.hf import hf_embed
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.utils import EmbeddingFunc
from transformers import AutoModel, AutoTokenizer

from deepinsight.config.config import Config
from deepinsight.config.rag_config import MineruParserConfig
from deepinsight.service.rag.backends.base import BaseRAGBackend
from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocProcessStatus, DocumentPayload, IndexResult, Passage
from deepinsight.utils.llm_utils import init_lightrag_llm_model_func


class LightRAGBackend(BaseRAGBackend):
    """Wrapper around LightRAG that supports async ingestion/search."""

    def __init__(self, config: Config, parser_config: Optional[MineruParserConfig] = None):
        self._config = config
        self._engine_cfg = config.rag.engine.lightrag
        self._parser_cfg = parser_config
        self._rag_cache: Dict[str, LightRAG] = {}
        self._rag_initialized: set[str] = set()
        self._llm_func = None

    async def ingest(
        self,
        payload: DocumentPayload,
        working_dir: str,
        parsed: LoaderOutput,
        *,
        make_knowledge_graph: bool | None = None,
    ) -> IndexResult:
        rag = await self._get_or_create_rag(working_dir)

        enable_graph = (
            self._engine_cfg.enable_graph_extraction if make_knowledge_graph is None else make_knowledge_graph
        )
        if not enable_graph:
            async def no_extract(*_args, **_kwargs):
                return "<|COMPLETE|>"
            rag.llm_model_func = no_extract
        else:
            rag.llm_model_func = self._llm_func

        parse_result = parsed.result
        if parse_result is None:
            raise ValueError("LightRAG ingestion requires parsed document result")

        text_chunks = parse_result.text or []
        text = "\n\n".join(chunk.page_content for chunk in text_chunks if chunk.page_content)
        documents_data = [
            {"page_content": chunk.page_content, "metadata": getattr(chunk, "metadata", {})}
            for chunk in text_chunks
        ]
        file_paths = parsed.file_paths or ([payload.source_path] if payload.source_path else payload.filename)

        await rag.ainsert([text], ids=[payload.doc_id], file_paths=file_paths)
        chunks_count = _estimate_chunks(text)
        process_status = await self._fetch_doc_status(rag, str(payload.doc_id))

        return IndexResult(
            doc_id=payload.doc_id,
            indexed=True,
            chunks_count=chunks_count,
            extracted_text=text,
            documents=documents_data,
            process_status=process_status,
        )

    async def retrieve(self, working_dir: str, query: str, top_k: int) -> List[Passage]:
        rag = await self._get_or_create_rag(working_dir)
        result = await rag.aquery_data(
            query=query,
            param=QueryParam(
                mode="hybrid",
                top_k=top_k,
            )
        )
        passages: List[Passage] = []
        if result["status"] != "success":
            raise Exception(f"light retrieve {result['status']} because {result['message']}")
        for chunk in result["data"]["chunks"]:
            passages.append(
                Passage(
                    chunk_id=chunk["chunk_id"],
                    text=chunk["content"],
                )
            )
        return passages

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
                    embedding_dim=self._engine_cfg.embedding_dim,
                    func=lambda texts: hf_embed(
                        texts,
                        tokenizer=AutoTokenizer.from_pretrained(self._engine_cfg.embedding_model),
                        embed_model=AutoModel.from_pretrained(self._engine_cfg.embedding_model),
                    ),
                ),
            )
            self._rag_cache[working_dir] = rag

        if working_dir not in self._rag_initialized:
            await rag.initialize_storages()
            await initialize_pipeline_status()
            self._rag_initialized.add(working_dir)
        return rag

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

            if isinstance(status, str):
                s = status.lower()
            else:
                s = getattr(status, "value", None)
                if isinstance(s, str):
                    s = s.lower()
                else:
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


def _estimate_chunks(text: str) -> int:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return max(1, len(paragraphs))
