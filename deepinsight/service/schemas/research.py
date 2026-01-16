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
    CROSS_TOPIC_RESEARCH = "cross_topic_research"


class RetrievalArgs(BaseModel):
    """Arguments for RAG retrieval configuration."""
    dialog_id: Optional[str] = Field(default=None, description="Dialog id")
    dataset_ids: Optional[List[str]] = Field(default_factory=list, description="List of dataset IDs")
    document_ids: Optional[List[str]] = Field(default_factory=list, description="List of document IDs")
    page: Optional[int] = Field(1, description="Page number for pagination")
    page_size: Optional[int] = Field(20, description="Number of items per page")
    similarity_threshold: Optional[float] = Field(0.3, description="Threshold for similarity")
    vector_similarity_weight: Optional[float] = Field(0.4, description="Weight for vector similarity")
    top_k: Optional[int] = Field(100, description="Top-K results to retrieve")
    top_n: Optional[int] = Field(3, description="Top-N results to retrieve")
    rerank_id: Optional[str] = Field(None, description="Re-rank model ID")
    keyword: Optional[bool] = Field(False, description="Enable keyword matching")
    highlight: Optional[bool] = Field(False, description="Enable text highlighting")


class ResearchArgs(BaseModel):
    """Optional arguments to customize research."""
    llm_options: Optional[List[ArgOptionsGeneric[LLMConfig]]] = Field(
        default=None, 
        description="LLM arguments"
    )
    retrieval_options: Optional[List[ArgOptionsGeneric[RetrievalArgs]]] = Field(
        default=None,
        description="Retrieval arguments for RAG"
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

    search_type: Optional[List[str]] = Field(
        None,
        description="List of search types to use: 'rag_retrieval', 'web_search'",
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

    def convert_search_type_to_search_api(self) -> List[SearchAPI]:
        """Convert user-facing search_type to internal SearchAPI enums.
        
        Returns:
            List of SearchAPI enum values based on search_type and scene_type
        """
        if not self.search_type:
            return [SearchAPI.TAVILY]  # Default to web search
        
        type_mapping = {
            "rag_retrieval": SearchAPI.RAG_RETRIVAL,
            "web_search": SearchAPI.TAVILY,
        }
        
        converted_types = []
        for st in self.search_type:
            if st in type_mapping:
                api_type = type_mapping[st]
                if api_type not in converted_types:
                    converted_types.append(api_type)
        
        # For conference scenarios, always include PAPER_STATIC_DATA
        if self.scene_type in [SceneType.CONFERENCE_RESEARCH, SceneType.CONFERENCE_QA, SceneType.CROSS_TOPIC_RESEARCH]:
            if SearchAPI.PAPER_STATIC_DATA not in converted_types:
                converted_types.append(SearchAPI.PAPER_STATIC_DATA)
        
        return converted_types if converted_types else [SearchAPI.TAVILY]


class PPTGenerateRequest(BaseModel):
    conversation_id: str = Field(...,
                                 description="Unique identifier of the conversation")
    args: Optional[ResearchArgs] = Field(None, description="Additional arguments for the conversation")

class PdfGenerateRequest(BaseModel):
    conversation_id: str = Field(...,
                                 description="Unique identifier of the conversation")
    args: Optional[ResearchArgs] = Field(None, description="Additional arguments for the conversation")
