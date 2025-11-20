from __future__ import annotations

from typing import Generic, Optional, TypeVar
from enum import Enum
from pydantic import BaseModel, Field

class OwnerType(str, Enum):
    """Common owner types for knowledge base binding."""
    CONFERENCE = "conference"
    # Future owners can be added here, e.g. WORKSPACE = "workspace", USER = "user"

T = TypeVar("T")

class ResponseModel(BaseModel, Generic[T]):
    code: int = Field(0, description="Response code")
    message: str = Field("ok", description="Response message")
    data: Optional[T] = Field(None, description="Response data")