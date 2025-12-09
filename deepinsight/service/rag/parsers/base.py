from __future__ import annotations

from abc import ABC, abstractmethod

from deepinsight.service.schemas.rag import DocumentPayload
from deepinsight.service.rag.types import LoaderOutput


class BaseDocumentParser(ABC):
    """Abstract parser interface to normalize document ingestion."""

    @abstractmethod
    async def parse(self, payload: DocumentPayload, kb_id: int, resource_prefix: str) -> LoaderOutput:
        raise NotImplementedError

