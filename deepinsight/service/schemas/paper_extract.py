from __future__ import annotations

from typing import Optional, List

from pydantic import BaseModel, Field, field_validator


class ExtractPaperMetaRequest(BaseModel):
    """Request schema for extracting paper metadata and persisting to DB."""
    conference_id: int = Field(..., description="Existing conference ID")

    filename: str = Field(..., description="关于paper的文件名")
    paper: str = Field(..., description="Paper content in Markdown format")


class AuthorMeta(BaseModel):
    name: str = Field(..., description="Author's full name")
    email: Optional[str] = Field("", description="Author's email address")
    address: Optional[str] = Field("", description="Author's mailing address")
    affiliation: Optional[str] = Field(
        "",
        description=(
            "Author's official institution or organization name. "
            "Must use the standardized and officially recognized name. "
            "For example, use 'University of California, Berkeley' instead of 'UC Berkeley'. "
            "Validation ensures the institution name exists in the official global institution registry."
        ),
    )

    affiliation_country: Optional[str] = Field(
        "",
        description=(
            "Official country name corresponding to the author's institution. "
            "Must use ISO 3166-1 English short name format (e.g., 'United States', 'United Kingdom'). "
            "Validation ensures the country exists in the official ISO country list."
        ),
    )

    affiliation_city: Optional[str] = Field(
        "",
        description=(
            "Official city name corresponding to the author's institution. "
            "Must use the official English name of the city (e.g., 'Beijing', 'San Francisco'). "
            "Validation ensures the city exists within the specified country using a verified city database."
        ),
    )


class AuthorInfo(BaseModel):
    first_author: Optional[AuthorMeta] = Field(None, description="Information of the first author")
    co_first_authors: List[AuthorMeta] = Field(default_factory=list, description="List of co-first authors")
    middle_authors: List[AuthorMeta] = Field(default_factory=list, description="List of middle authors")
    last_authors: List[AuthorMeta] = Field(default_factory=list, description="Information of the last author")
    corresponding_authors: List[AuthorMeta] = Field(default_factory=list, description="List of corresponding authors")


class PaperMeta(BaseModel):
    paper_title: str = Field(..., description="Title of the paper")
    author_info: AuthorInfo = Field(..., description="Detailed author information")
    abstract: str = Field("", description="Abstract of the paper")
    keywords: List[str] = Field(default_factory=list, description="List of keywords")
    topic: str = Field(..., description="Main topic of the paper")

    @property
    def all_authors(self) -> List[AuthorMeta]:
        first = [self.author_info.first_author] if self.author_info.first_author is not None else []
        return (first + self.author_info.co_first_authors + self.author_info.middle_authors +
                self.author_info.last_authors + self.author_info.corresponding_authors)


class DocSegment(BaseModel):
    content: str = Field(..., description="Segment content (text)")
    metadata: Optional[dict] = Field(default_factory=dict, description="Optional metadata for the segment")


class ExtractPaperMetaFromDocsRequest(BaseModel):
    """Request schema for extracting paper metadata from pre-parsed documents."""
    conference_id: int = Field(..., description="Existing conference ID")
    filename: str = Field(..., description="关于paper的文件名")
    documents: List[DocSegment] = Field(..., description="Parsed document segments with content and metadata")


class ExtractPaperMetaResponse(BaseModel):
    paper_id: int = Field(..., description="ID of the extracted paper")
    title: str = Field(..., description="Title of the paper")
    conference_id: int = Field(..., description="ID of the conference")
    author_ids: List[int] = Field(default_factory=list, description="List of author IDs")
    topic: Optional[str] = Field(None, description="Main topic of the paper")
