from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional, Dict, Any, List, Union

from pydantic import BaseModel, Field, field_validator, ConfigDict


# ===== Request Schemas =====
class ConferenceCreateRequest(BaseModel):
    """Request schema for creating a conference record"""
    full_name: str = Field(..., description="Conference full name")
    short_name: Optional[str] = Field(None, description="Conference short name (acronym)")
    year: int = Field(..., description="Year of the conference")
    location: Optional[str] = Field(None, description="City or venue")
    start_date: Optional[date] = Field(None, description="Start date (YYYY-MM-DD)")
    end_date: Optional[date] = Field(None, description="End date (YYYY-MM-DD)")
    website: Optional[str] = Field(None, description="Official website URL")
    topics: Optional[List[str]] = Field(None, description="Topics as list of strings")

    @field_validator("topics", mode="before")
    def _parse_topics(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"topics must be a valid JSON string: {e}")
        raise ValueError("topics must be list of strings")


class ConferenceListRequest(BaseModel):
    """Request schema for listing conferences with filters"""
    short_name: Optional[str] = None
    year: Optional[int] = None
    location: Optional[str] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class ConferenceUpdateRequest(BaseModel):
    """Request schema for updating a conference record"""
    conference_id: int = Field(..., ge=1)
    full_name: Optional[str] = None
    short_name: Optional[str] = None
    year: Optional[int] = None
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    website: Optional[str] = None
    topics: Optional[Dict[str, Any]] = None

    @field_validator("topics", mode="before")
    def _parse_topics(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"topics must be a valid JSON string: {e}")
        raise ValueError("topics must be dict or JSON string")


class ConferenceDeleteRequest(BaseModel):
    """Request schema for deleting a conference"""
    conference_id: int = Field(..., ge=1)


class ConferenceParseDocsRequest(BaseModel):
    """CLI-friendly request to parse docs for a conference.
    - If conference does not exist, create and ingest.
    - If exists, perform incremental ingestion.
    """
    # Identify or create conference
    full_name: Optional[str] = None
    short_name: Optional[str] = None
    year: Optional[int] = None
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    website: Optional[str] = None
    topics: Optional[Dict[str, Any]] = None

    # Source docs folder (user provided)
    docs_src_dir: str = Field(..., description="User-provided folder containing documents")

    # Parsing options
    parser: Optional[str] = None
    parse_method: Optional[str] = None
    embed_model: Optional[str] = None

    # File type filter
    exts: List[str] = Field(default_factory=lambda: [
        ".pdf", ".md", ".txt", ".doc", ".docx", ".ppt", ".pptx"
    ])

    @field_validator("topics", mode="before")
    def _parse_topics(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"topics must be a valid JSON string: {e}")
        raise ValueError("topics must be dict or JSON string")


class GenerateKnowledgeBaseRequest(BaseModel):
    """Request to generate a knowledge base for a conference"""
    conference_id: int = Field(..., description="The ID of the conference")
    docs_root_dir: str = Field(..., description="Root directory of the documents for the knowledge base")
    parser: Optional[str] = None
    parse_method: Optional[str] = None
    embed_model: Optional[str] = None


# ===== Response Schemas =====
class ConferenceResponse(BaseModel):
    conference_id: int
    full_name: str
    short_name: Optional[str]
    year: int
    location: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    website: Optional[str]
    topics: Optional[Union[List[str], Dict[str, Any]]]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ConferenceListResponse(BaseModel):
    items: List[ConferenceResponse]
    count: int


class DeleteConferenceResponse(BaseModel):
    ok: bool


__all__ = [
    # Requests
    "ConferenceCreateRequest",
    "ConferenceListRequest",
    "ConferenceUpdateRequest",
    "ConferenceDeleteRequest",
    "GenerateKnowledgeBaseRequest",
    "ConferenceParseDocsRequest",
    # Responses
    "ConferenceResponse",
    "ConferenceListResponse",
    "DeleteConferenceResponse",
]