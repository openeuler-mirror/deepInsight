from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocumentPayload, IndexResult, Passage


class BaseRAGBackend(ABC):
    """Common interface shared by RAG backends."""

    @abstractmethod
    async def ingest(
        self,
        payload: DocumentPayload,
        working_dir: str,
        parsed: LoaderOutput,
        *,
        make_knowledge_graph: bool | None = None,
    ) -> IndexResult:
        raise NotImplementedError

    @abstractmethod
    async def retrieve(self, working_dir: str, query: str, top_k: int) -> List[Passage]:
        raise NotImplementedError

