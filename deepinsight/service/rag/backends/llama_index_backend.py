from __future__ import annotations

import asyncio
import os
from typing import Any, List, Optional, Tuple

from llama_index.core import Document as LIDocument
from llama_index.core import Settings, StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.schema import NodeWithScore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.openai import OpenAI

from deepinsight.config.config import Config
from deepinsight.config.rag_config import LlamaIndexEngineConfig
from deepinsight.service.rag.backends.base import BaseRAGBackend
from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocProcessStatus, DocumentPayload, IndexResult, Passage


class LlamaIndexBackend(BaseRAGBackend):
    """Backend powered by LlamaIndex vector store."""

    def __init__(self, config: Config):
        self._config = config
        self._engine_cfg: LlamaIndexEngineConfig = config.rag.engine.llamaindex
        self._storage_cache: dict[str, StorageContext] = {}
        self._index_cache: dict[str, VectorStoreIndex] = {}
        self._init_global_settings()

    async def ingest(
        self,
        payload: DocumentPayload,
        working_dir: str,
        parsed: LoaderOutput,
        *,
        make_knowledge_graph: bool | None = None,
    ) -> IndexResult:
        _ = make_knowledge_graph
        storage, index = await self._get_or_create_storage_and_index(working_dir)
        documents = [
            LIDocument(id_=payload.doc_id, text=chunk.page_content, metadata=getattr(chunk, "metadata", {}))
            for chunk in parsed.result.text
        ]
        for doc in documents:
            await index.ainsert(doc)
        storage.persist(working_dir)
        extracted_text = "\n\n".join(doc.text for doc in documents if doc.text)
        documents_data = [
            {"page_content": chunk.page_content, "metadata": getattr(chunk, "metadata", {})}
            for chunk in parsed.result.text
        ]
        return IndexResult(
            doc_id=payload.doc_id,
            indexed=True,
            chunks_count=max(1, len(documents)),
            extracted_text=extracted_text,
            documents=documents_data,
            process_status=DocProcessStatus.parsed,
        )

    async def retrieve(self, working_dir: str, query: str, top_k: int) -> List[Passage]:
        storage, index = await self._get_or_create_storage_and_index(working_dir)
        retriever = index.as_retriever(similarity_top_k=top_k)
        response: List[NodeWithScore] = retriever.retrieve(query)
        passages: List[Passage] = []
        for each in response:
            passages.append(
                Passage(
                    chunk_id=each.node_id,
                    text=each.text,
                    score=each.score,
                    meta=each.metadata,
                )
            )
        return passages

    async def _get_or_create_storage_and_index(self, working_dir: str) -> Tuple[StorageContext, VectorStoreIndex]:
        if working_dir in self._storage_cache:
            os.makedirs(working_dir, exist_ok=True)
            return self._storage_cache[working_dir], self._index_cache[working_dir]
        try:
            storage_context = StorageContext.from_defaults(persist_dir=working_dir)
        except FileNotFoundError:
            storage_context = StorageContext.from_defaults()
        self._storage_cache[working_dir] = storage_context
        if os.listdir(working_dir):
            index = await asyncio.to_thread(load_index_from_storage, storage_context)
        else:
            index = VectorStoreIndex([], storage_context=storage_context)
        self._index_cache[working_dir] = index
        return storage_context, index
        

    def _init_global_settings(self) -> None:
        Settings.embed_model = HuggingFaceEmbedding(
            model_name=self._engine_cfg.embed_model,
            device=self._engine_cfg.embed_device,
        )
        llm_model = self._engine_cfg.llm_model
        llm_api_key = self._engine_cfg.llm_api_key
        llm_base_url = self._engine_cfg.llm_base_url
        if not llm_model:
            default_llm = (self._config.llms[0] if self._config.llms else None)
            llm_model = getattr(default_llm, "model", None)
            llm_api_key = llm_api_key or getattr(default_llm, "api_key", None)
            llm_base_url = llm_base_url or getattr(default_llm, "base_url", None)
        if not llm_model:
            raise ValueError("LlamaIndex backend requires llm_model configuration.")
        Settings.llm = OpenAI(model=llm_model, api_key=llm_api_key, base_url=llm_base_url)

