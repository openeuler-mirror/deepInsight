from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict
from deepinsight.service.schemas.common import OwnerType


# ===== 请求模型 =====
class KnowledgeBaseCreateRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    owner_type: OwnerType = Field(default=OwnerType.CONFERENCE)
    owner_id: Optional[int] = None
    root_dir: str
    index_dir: Optional[str] = None
    parser: Optional[str] = None
    parse_method: Optional[str] = None
    embed_model: Optional[str] = None


class KnowledgeDocumentCreateRequest(BaseModel):
    kb_id: int
    file_path: str
    file_name: Optional[str] = None
    md5: Optional[str] = None


class ScanAndRegisterRequest(BaseModel):
    kb_id: int
    root_dir: Optional[str] = None
    exts: Tuple[str, ...] = (".pdf",)
    # 新增：从外部传递会议ID，用于联动论文提取
    conference_id: Optional[int] = None


class BeginProcessingRequest(BaseModel):
    kb_id: int


class FinalizeRequest(BaseModel):
    kb_id: int
    owner_id: Optional[int] = None


class KnowledgeListRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    owner_type: Optional[OwnerType] = None
    owner_id: Optional[int] = None
    status: Optional[str] = None
    limit: int = 100
    offset: int = 0


class KnowledgeDeleteRequest(BaseModel):
    kb_id: int


class KnowledgeSearchRequest(BaseModel):
    """统一的知识检索请求体"""
    kb_id: int = Field(..., description="知识库ID")
    query: str = Field(..., description="检索查询语句")
    top_k: int = Field(5, ge=1, le=100, description="返回TopK条目")


# ===== 响应模型 =====
class KnowledgeDocStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    parsed = "parsed"
    failed = "failed"

class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kb_id: int
    owner_type: str
    owner_id: Optional[int]
    root_dir: str
    index_dir: Optional[str]
    parser: Optional[str]
    parse_method: Optional[str]
    embed_model: Optional[str]
    status: str
    doc_count: int
    last_built_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    doc_id: int
    kb_id: int
    file_path: str
    file_name: str
    parse_status: KnowledgeDocStatus
    chunks_count: int
    extracted_text: Optional[str] = None
    # New: expose parsed documents from LangChain loaders for downstream processing
    documents: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    updated_at: datetime


class KnowledgeListResponse(BaseModel):
    items: List[KnowledgeBaseResponse]
    total: int