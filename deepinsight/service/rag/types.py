from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from langchain_core.documents import Document

from deepinsight.service.rag.loaders.base import ParseResult


@dataclass
class LoaderOutput:
    """Unified output produced by document parsing pipelines."""

    result: ParseResult
    file_paths: Optional[List[str]] = None

    @property
    def documents(self) -> List[Document]:
        return self.result.text


