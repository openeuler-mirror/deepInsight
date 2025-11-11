from __future__ import annotations

from typing import Any, Dict, Optional, List

from pydantic import BaseModel, Field


class DocumentPayload(BaseModel):
    """Standardized document payload for RAG ingestion.

    Fields:
    - doc_id: unique document id (idempotency key)
    - raw_text: plain text content
    - source_path: original file path (optional)
    - title: optional title
    - hash: content hash for dedup (optional)
    - origin: source tag, e.g. 'conference-cli'
    - metadata: extra metadata (optional)
    """

    doc_id: str = Field(..., description="Unique document ID")
    raw_text: str = Field(..., description="Document plain text")
    source_path: Optional[str] = Field(None, description="Original file path")
    title: Optional[str] = Field(None, description="Title")
    hash: Optional[str] = Field(None, description="Content hash")
    origin: Optional[str] = Field(None, description="Source tag")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Extra metadata")


class IndexResult(BaseModel):
    """Indexing result."""

    doc_id: str
    indexed: bool
    chunks_count: int
    extracted_text: Optional[str] = Field(None, description="Extracted plain text for downstream usage")
    # New: return parsed documents (LangChain-compatible) as simple dicts
    documents: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="List of parsed documents with 'page_content' and 'metadata' keys",
    )


class Passage(BaseModel):
    """Search evidence chunk."""

    doc_id: str
    chunk_id: str
    text: str
    score: float
    meta: Optional[Dict[str, Any]] = Field(default_factory=dict)


__all__ = [
    "DocumentPayload",
    "IndexResult",
    "Passage",
]