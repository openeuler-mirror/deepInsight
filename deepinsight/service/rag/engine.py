from __future__ import annotations

import os
from typing import List, Optional
import copy
import logging

from langchain_core.documents import Document as LCDocument

import deepinsight.config.config as config_file
from deepinsight.config.config import Config
from deepinsight.config.rag_config import RAGEngineType, RAGParserType
from deepinsight.service.rag.backends import (
    LightRAGBackend,
    LlamaIndexBackend,
)
from deepinsight.service.rag.backends.base import BaseRAGBackend
from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.service.rag.parsers import LlamaIndexParser, MineruVLParser
from deepinsight.service.rag.parsers.base import BaseDocumentParser
from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocumentPayload, IndexResult, Passage
from deepinsight.core.types.graph_config import RetrievalConfig, RetrievalType
from langchain_core.tools import Tool, tool as make_tool
from deepinsight.databases.connection import Database
from deepinsight.databases.models.knowledge import KnowledgeBase


class RAGEngine:
    """Configurable orchestration layer that wires parser and backend implementations."""

    def __init__(self, config: Optional[Config] = None):
        if config is None:
            from deepinsight.config.config import CONFIG
            config = CONFIG
        self._config = config
        if self._config is None:
            raise RuntimeError("Config not initialized; load config before using RAG engine")
        self._backend: BaseRAGBackend = self._build_backend()
        self._parser: Optional[BaseDocumentParser] = self._build_parser()

    async def ingest_document(
        self,
        doc: DocumentPayload,
        working_dir: str, kb_id: int,
        make_knowledge_graph: bool | None = None,
    ) -> IndexResult:
        if not working_dir:
            raise ValueError("working_dir must not be empty")
        os.makedirs(working_dir, exist_ok=True)
        parsed = await self._prepare_document(doc, kb_id)
        return await self._backend.ingest(
            doc,
            working_dir,
            parsed,
            make_knowledge_graph=make_knowledge_graph,
        )

    async def retrieve(self, working_dir: str, query: str, top_k: int = 8) -> List[Passage]:
        return await self._backend.retrieve(working_dir, query, top_k)

    @classmethod
    def from_retrieval_config(cls, retrieval_config: RetrievalConfig, config: Optional[Config] = None) -> "RAGEngine":
        """Create a RAGEngine instance from a RetrievalConfig."""
        if config is None:
            from deepinsight.config.config import CONFIG
            config = CONFIG
        base_config = config
        if base_config is None:
            raise RuntimeError("Base config not initialized")
        
        # Create a copy of the config to avoid modifying the global one
        engine_config = copy.deepcopy(base_config)
        
        # Map RetrievalType to RAGEngineType
        if retrieval_config.type == RetrievalType.LIGHTRAG:
            engine_config.rag.engine.type = RAGEngineType.lightrag
        elif retrieval_config.type == RetrievalType.LLAMAINDEX:
            engine_config.rag.engine.type = RAGEngineType.llamaindex
        else:
            raise ValueError(f"Unsupported retrieval type for RAGEngine: {retrieval_config.type}")
            
        return cls(engine_config)

    def as_tool(self, retrieval_config: RetrievalConfig) -> Tool:
        """Create a LangChain tool from this engine instance."""
        
        async def retrieve_func(question: str):
            kb_ids = retrieval_config.args.kb_ids
            if not kb_ids:
                return "[]"

            top_k = retrieval_config.args.top_k or 10
            all_passages = []
            
            # Initialize database connection
            db = Database(self._config.database)
            
            with db.get_session() as session:
                for kb_id in kb_ids:
                    # Resolve working_dir from DB
                    # Try to treat kb_id as integer ID first
                    try:
                        kid = int(kb_id)
                        kb = session.query(KnowledgeBase).filter(KnowledgeBase.kb_id == kid).first()
                        if kb:
                            working_dir = kb.index_dir or os.path.join(self._config.rag.work_root, "rag_storage", str(kb.kb_id))
                        else:
                            # Fallback if not found in DB but might be a path or ID
                            working_dir = os.path.join(self._config.rag.work_root, "rag_storage", str(kb_id))
                    except ValueError:
                        # If kb_id is not an int, check if it's a path
                        if os.path.isabs(kb_id) or "/" in kb_id:
                            working_dir = kb_id
                        else:
                            working_dir = os.path.join(self._config.rag.work_root, "rag_storage", str(kb_id))
                    
                    try:
                        passages = await self.retrieve(working_dir, question, top_k)
                        all_passages.extend(passages)
                    except Exception as e:
                        logging.warning(f"Failed to retrieve from KB {kb_id} (path: {working_dir}): {e}")

            # Sort combined results by score (descending) and take top_k
            # Note: Scores across different indices might not be perfectly comparable, but it's a best effort
            all_passages.sort(key=lambda p: p.score, reverse=True)
            final_passages = all_passages[:top_k]
            
            # Format results
            import json
            results = [
                {
                    "chunk_id": passage.chunk_id,
                    "text": passage.text,
                    "score": passage.score,
                }
                for passage in final_passages
            ]
            return json.dumps(results, indent=4, ensure_ascii=False)

        def sync_retrieve_func(question: str):
            """
            Core retrieval tool for the RAG workflow: extracts highly relevant knowledge chunks
            from the specified knowledge base based on the input question. 

            Args:
                question (str): The question used for knowledge retrieval.
                    The question must be a complete, meaningful sentence containing key
                    entities (e.g., "2024 new-energy vehicles") and clear qualifiers
                    (e.g., "year-over-year growth", "regulatory policy"). Avoid vague
                    expressions such as "How do I do this?"
            """

            import asyncio
            import warnings
            
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            
            if loop and loop.is_running():
                # If we are in a running loop, use a thread pool to run asyncio.run in a separate thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    # Use asyncio.run which properly handles cleanup
                    future = pool.submit(asyncio.run, retrieve_func(question))
                    return future.result()
            else:
                # Create a new event loop for this synchronous call
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result = new_loop.run_until_complete(retrieve_func(question))
                    # Give pending tasks a chance to complete before closing the loop
                    # This prevents "Event loop is closed" errors from httpx cleanup
                    pending = asyncio.all_tasks(new_loop)
                    if pending:
                        new_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    return result
                finally:
                    try:
                        # Properly shutdown async generators
                        new_loop.run_until_complete(new_loop.shutdown_asyncgens())
                        # Close the loop
                        new_loop.close()
                    except Exception:
                        # Suppress any errors during cleanup
                        pass

        def _create_tool_description(f):
            tool = make_tool(f, parse_docstring=True)
            return dict(description=tool.description, args_schema=tool.args_schema)

        return Tool.from_function(
            func=sync_retrieve_func,
            name=f"{retrieval_config.type}_knowledge_retrieve",
            coroutine=retrieve_func,
            **_create_tool_description(sync_retrieve_func)
        )

    def _build_backend(self) -> BaseRAGBackend:
        engine_type = self._config.rag.engine.type
        if engine_type == RAGEngineType.lightrag:
            parser_cfg = self._config.rag.parser
            mineru_cfg = parser_cfg.mineru_vl if parser_cfg.type == RAGParserType.mineru_vl else None
            return LightRAGBackend(self._config, parser_config=mineru_cfg)
        if engine_type == RAGEngineType.llamaindex:
            return LlamaIndexBackend(self._config)
        raise ValueError(f"Unsupported RAG engine type: {engine_type}")

    def _build_parser(self) -> Optional[BaseDocumentParser]:
        parser_cfg = self._config.rag.parser
        parser_type = parser_cfg.type
        if parser_type == RAGParserType.mineru_vl:
            return MineruVLParser(parser_cfg.mineru_vl)
        if parser_type == RAGParserType.llamaindex:
            return LlamaIndexParser(parser_cfg.llamaindex)
        return None

    async def _prepare_document(self, doc: DocumentPayload, kb_id: int) -> LoaderOutput:
        if doc.raw_text and doc.raw_text.strip():
            parse_result = ParseResult(
                text=[
                    LCDocument(
                        page_content=doc.raw_text,
                        metadata={"source": doc.source_path or "inline", **(doc.metadata or {})},
                    )
                ]
            )
            file_paths = [doc.source_path] if doc.source_path else doc.filename
            return LoaderOutput(result=parse_result, file_paths=file_paths)

        if not self._parser:
            raise ValueError("Document parser not configured, raw_text missing.")
        return await self._parser.parse(doc, kb_id, config_file.CONFIG.workspace.resource_base_uri)


__all__ = [
    "RAGEngine",
]
