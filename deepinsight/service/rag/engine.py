from __future__ import annotations

import os
from typing import List, Optional

from langchain_core.documents import Document as LCDocument

from deepinsight.config.config import CONFIG, Config
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


class RAGEngine:
    """Configurable orchestration layer that wires parser and backend implementations."""

    def __init__(self, config: Optional[Config] = None):
        self._config = config or CONFIG
        if self._config is None:
            raise RuntimeError("Config not initialized; load config before using RAG engine")
        self._backend: BaseRAGBackend = self._build_backend()
        self._parser: Optional[BaseDocumentParser] = self._build_parser()

    async def ingest_document(
        self,
        doc: DocumentPayload,
        working_dir: str,
        make_knowledge_graph: bool | None = None,
    ) -> IndexResult:
        if not working_dir:
            raise ValueError("working_dir must not be empty")
        os.makedirs(working_dir, exist_ok=True)
        parsed = await self._prepare_document(doc, working_dir)
        return await self._backend.ingest(
            doc,
            working_dir,
            parsed,
            make_knowledge_graph=make_knowledge_graph,
        )

    async def retrieve(self, working_dir: str, query: str, top_k: int = 8) -> List[Passage]:
        return await self._backend.retrieve(working_dir, query, top_k)

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

    async def _prepare_document(self, doc: DocumentPayload, working_dir: str) -> LoaderOutput:
        if doc.raw_text and doc.raw_text.strip():
            parse_result = ParseResult(
                text=[
                    LCDocument(
                        page_content=doc.raw_text,
                        metadata={"source": doc.source_path or "inline", **(doc.metadata or {})},
                    )
                ]
            )
            file_paths = [doc.source_path] if doc.source_path else None
            return LoaderOutput(result=parse_result, file_paths=file_paths)

        if not self._parser:
            raise ValueError("Document parser not configured, raw_text missing.")
        return await self._parser.parse(doc, working_dir)


__all__ = [
    "RAGEngine",
]
