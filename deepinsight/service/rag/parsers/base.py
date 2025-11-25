from __future__ import annotations

from abc import ABC, abstractmethod

from deepinsight.service.schemas.rag import DocumentPayload
from deepinsight.service.rag.types import LoaderOutput


class BaseDocumentParser(ABC):
    """Abstract parser interface to normalize document ingestion."""

    @abstractmethod
    async def parse(self, payload: DocumentPayload, working_dir: str) -> LoaderOutput:
        raise NotImplementedError

