# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from __future__ import annotations

from typing import Any, Dict, Optional, List
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from deepinsight.core.prompt.prompt_manager import PromptManager

class SearchAPI(str, Enum):
    """Enumeration of available search API providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    TAVILY = "tavily"
    SQL_DATA = "sql_data"
    RAG_RETRIVAL = "rag_retrival"
    PAPER_STATIC_DATA = "paper_static_data"
    NONE = "none"

class ResearchConfig(BaseModel):
    """Typed structure for LangGraph configurable options.

    Mirrors the `configurable` section from graph runtime config.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    # Identifiers optionally propagated into configurable
    run_id: Optional[str] = Field(default=None, description="Unique run identifier if present")
    thread_id: Optional[str] = Field(default=None, description="Thread identifier used to scope runs")

    # LangChain models (see init_langchain_models_from_llm_config)
    models: Dict[str, BaseChatModel] = Field(
        default_factory=dict,
        description="Map of 'provider:model' -> BaseChatModel instance",
    )
    default_model: Optional[BaseChatModel] = Field(
        default=None,
        description="Default BaseChatModel instance",
    )

    # Generation and parsing settings
    llm_max_tokens: int = Field(default=8192, description="Max tokens for LLM responses")
    max_content_length: int = Field(default=60000, description="Max window length for LLM")
    max_structured_output_retries: int = Field(default=3, description="Retries for structured output validation")
    max_react_tool_calls: int = Field(default=5, description="Max tool calls per researcher iteration")
    
    # Research flow control
    max_concurrent_research_units: int = Field(default=5, description="Max concurrent research units")
    max_researcher_iterations: int = Field(default=10, description="Max iterations per researcher")
        
    # Interactive research flags
    allow_user_clarification: bool = Field(default=False)
    allow_edit_research_brief: bool = Field(default=False)
    allow_edit_report_outline: bool = Field(default=False)

    # Optional hints
    final_report_model: Optional[str] = Field(default=None, description="Preferred model name for final report generation")
    prompt_group: str = Field(default="research", description="Prompt group hint used by graph nodes")

    search_api: List[SearchAPI] = Field(
        default=[SearchAPI.TAVILY],  # 默认值改为包含单个元素的列表
        metadata={
            "x_oap_ui_config": {
                "type": "multiselect",  # 类型改为多选
                "default": ["tavily"],  # 默认值改为数组形式
                "description": "Search APIs to use for research. NOTE: Make sure your Researcher Model supports the selected search APIs.",
                "options": [
                    {"label": "Tavily", "value": SearchAPI.TAVILY},
                    {"label": "OpenAI Native Web Search", "value": SearchAPI.OPENAI},
                    {"label": "Anthropic Native Web Search", "value": SearchAPI.ANTHROPIC},
                    {"label": "SQL Data Query", "value": SearchAPI.SQL_DATA},
                    {"label": "RAG Retrieval", "value": SearchAPI.RAG_RETRIVAL},
                    {"label": "None", "value": SearchAPI.NONE},
                ]
            }
        }
    )

    # Additional LangChain tools configured from service layer
    tools: List[BaseTool] = Field(
        default_factory=list,
        description="Additional LangChain tools provided by service to expose in research",
    )

    # Optional components occasionally present in config
    prompt_manager: Optional[PromptManager] = Field(default=None, description="Prompt manager instance if provided")

    # Working path configuration injected from global config.yaml
    work_root: Optional[str] = Field(
        default=None,
        description="Base working path for outputs and storage (from config.yaml rag.work_root)",
    )

    def get_model(self, provider_and_name: Optional[str] = None) -> Optional[BaseChatModel]:
        """Return a model backend instance.

        - If `provider_and_name` is provided, returns models[provider_and_name] when available.
        - Otherwise returns the default BaseChatModel instance.
        - Fallback: first available model in `models`.
        """
        if provider_and_name:
            return self.models.get(provider_and_name)
        if self.default_model:
            return self.default_model
        # Fallback: first available model
        return next(iter(self.models.values()), None)