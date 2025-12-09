from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict

from langchain_core.documents import Document as LCDocument

from deepinsight.config.rag_config import LlamaIndexParserConfig
from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.service.rag.parsers.base import BaseDocumentParser
from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocumentPayload


class LlamaIndexParser(BaseDocumentParser):
    """Parser backed by LlamaIndex readers."""

    def __init__(self, config: LlamaIndexParserConfig):
        self._config = config
        self._file_extractor = self._init_file_extractors(config)

    async def parse(self, payload: DocumentPayload, kb_id: int, resource_prefix: str) -> LoaderOutput:
        if not payload.source_path:
            raise ValueError("LlamaIndex parser requires payload.source_path to be provided")
        file_path = payload.source_path
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)

        docs = await self._read_with_llama_index(file_path)
        if not docs:
            raise RuntimeError(f"LlamaIndex parser failed to load content from {file_path}")
        lc_docs = [LCDocument(page_content=d.text, metadata=_normalize_metadata(d.metadata, file_path)) for d in docs]
        return LoaderOutput(result=ParseResult(text=lc_docs), file_paths=[file_path])

    async def _read_with_llama_index(self, file_path: str):
        from llama_index.core import SimpleDirectoryReader

        reader = SimpleDirectoryReader(
            input_files=[file_path],
            filename_as_id=True,
            file_extractor=self._file_extractor,
        )
        return await reader.aload_data()

    @staticmethod
    def _init_file_extractors(config: LlamaIndexParserConfig):
        if not config.use_llama_parse:
            return None
        try:
            from llama_parse import LlamaParse
        except Exception as e:
            logging.warning("Failed to import llama_parse: %s. Falling back to default reader.", e)
            return None

        api_key = config.api_key or os.environ.get("LLAMA_PARSE_API_KEY")
        if not api_key:
            logging.warning("LLAMA_PARSE_API_KEY not provided, disable LlamaParse extractor.")
            return None
        parser = LlamaParse(api_key=api_key, premium=config.premium, num_workers=1, split_by_page=True)
        return {".pdf": parser, ".docx": parser, ".pptx": parser}


def _normalize_metadata(meta: Dict[str, Any] | None, file_path: str) -> Dict[str, Any]:
    meta = meta or {}
    if "source" not in meta:
        meta["source"] = file_path
    return meta

