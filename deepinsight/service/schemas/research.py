from __future__ import annotations

from typing import Optional, List, TypeVar, Generic
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict

from deepinsight.core.types.graph_config import SearchAPI
from deepinsight.config.llm_config import LLMConfig
from deepinsight.service.schemas.streaming import Message


T = TypeVar("T")

class ArgOptionsGeneric(BaseModel, Generic[T]):
    type: str = Field(..., description="Arg option item type")
    params: T = Field(..., description="Arg option item params")


class SceneType(str, Enum):
    """场景类型枚举，用于选择具体的图实现。"""
    DEEP_RESEARCH = "deep_research"
    CONFERENCE_RESEARCH = "conference_research"
    CONFERENCE_QA = "conference_qa"


class ResearchArgs(BaseModel):
    """Optional arguments to customize research."""
    llm_options: Optional[List[ArgOptionsGeneric[LLMConfig]]] = Field(
        default=None, 
        description="LLM arguments"
    )


class ResearchRequest(BaseModel):
    """Request payload for research API."""
    # 使用枚举值进行序列化
    model_config = ConfigDict(use_enum_values=True)

    conversation_id: str = Field(..., description="Unique identifier of the conversation/session")
    messages: List[Message] = Field(..., description="List of messages in the conversation")
    scene_type: Optional[SceneType] = Field(
        None,
        description="Conversation scene type: research or conference",
    )

    search_api: Optional[List[SearchAPI]] = Field(
        None,
        description="List of search API providers to use (Anthropic, OpenAI, Tavily, etc.)",
    )

    # Optional behavior flags; override scenario config when provided
    allow_user_clarification: Optional[bool] = Field(None, description="Enable interactive user clarification")
    allow_edit_research_brief: Optional[bool] = Field(None, description="Allow editing research brief interactively")
    allow_edit_report_outline: Optional[bool] = Field(None, description="Allow editing final report outline")
    final_report_model: Optional[str] = Field(None, description="Preferred model name for final report generation")

    # Optional args bundle (e.g., LLM options)
    args: Optional[ResearchArgs] = Field(None, description="Additional options for execution")

    review_experts: Optional[List[str]] = Field(None)
    expert_review_enable: Optional[bool] = Field(False)
    parallel_expert_review_enable: Optional[bool] = Field(False)
    expert_name: Optional[str] = Field(None)
    write_experts: Optional[List[str]] = Field(None)


class PPTGenerateRequest(BaseModel):
    conversation_id: str = Field(...,
                                 description="Unique identifier of the conversation")
    args: Optional[ResearchArgs] = Field(None, description="Additional arguments for the conversation")

class PdfGenerateRequest(BaseModel):
    conversation_id: str = Field(...,
                                 description="Unique identifier of the conversation")
    args: Optional[ResearchArgs] = Field(None, description="Additional arguments for the conversation")
