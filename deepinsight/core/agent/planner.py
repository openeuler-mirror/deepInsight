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

import logging
import re
from copy import deepcopy
from enum import Enum
from typing import Any, Dict, List, Optional, TypeAlias, Callable, Generator

from camel.messages import BaseMessage
from camel.responses import ChatAgentResponse
from camel.types import OpenAIBackendRole
from pydantic import BaseModel, Field
from typing_extensions import override

from deepinsight.config.model import ModelConfig
from deepinsight.core.agent.base import BaseAgent
from deepinsight.core.prompt.prompt_template import GLOBAL_DEFAULT_PROMPT_REPOSITORY, PromptStage, PromptTemplate
from deepinsight.core.types.historical_message import HistoricalMessage, HistoricalMessageType
from deepinsight.core.types.messages import Message


class NotSupportStreamException(Exception):
    """Exception raised when streaming is not supported by the current agent configuration."""
    pass


class NoPlanException(Exception):
    """Exception raised when want to research but no valid plan."""
    pass


class SearchPlan(BaseModel):
    """
    A data model representing a research search plan.

    This class defines the structure for storing and validating information about
    a research plan, including its title, description, and original plan content.
    Inherits from Pydantic's BaseModel for data validation and serialization.
    """

    title: str
    """
    The title or name of the research plan.

    Attributes:
        str: A concise, descriptive title that summarizes the research focus.
        Required field (no default value).
    Example:
        "Optimizing Transformer Models for Edge Devices"
    """

    description: str = ""
    """
    Detailed description of the research plan.

    Attributes:
        str: A comprehensive explanation of the research objectives, scope, and methodology.
        Optional field (defaults to empty string if not provided).
    Example:
        "This plan explores quantization and pruning techniques to reduce transformer model size..."
    """

    origin_plan: str
    """
    The original, unmodified version of the research plan.

    Attributes:
        str: Contains the complete text of the initial research plan before any modifications.
        Serves as a reference point for tracking changes during plan iterations.
        Required field (no default value).
    Example:
        "1. Literature review on model compression techniques\n2. Implement baseline transformer model..."
    """


class PlanStatus(str, Enum):
    """Enum representing possible plan statuses"""
    DRAFT = "draft"
    FINALIZED = "finalized"
    REJECTED = "rejected"
    INCOMPLETE_INFO = "incomplete_info"  # Missing information cases, need supply


class PlanResult(BaseModel):
    """
    Result container for multi-alternative planning operations.
    Represents multiple plan alternatives generated for a single user query.
    """

    search_plans: Optional[List[SearchPlan]] = Field(default=None)
    """
    Alternative plans generated for the same research question.

    Attributes:
        Optional[List[SearchPlan]]: 
            ▪ None when need additional info needed

            ▪ Contains parallel alternative approaches to the same problem

            ▪ Each plan represents a complete, independent solution

            ▪ Ordered by recommended priority (index 0 is most recommended)


    Example:
        [
            SearchPlan(title="Quantization Approach", ...),
            SearchPlan(title="Pruning Approach", ...),
            SearchPlan(title="Architecture Search Approach", ...)
        ]
    """

    status: PlanStatus = PlanStatus.DRAFT
    """
    Current state of the planning process.

    Attributes:
        PlanStatus:
            ▪ DRAFT: Initial state, alternatives being considered

            ▪ FINALIZED: Plan selected and ready for execution

            ▪ REJECTED: All alternatives rejected


    Example:
        PlanStatus.FINALIZED
    """

    information_required: Optional[str] = Field(default=None)
    """
    Specific information needed from user when status=INCOMPLETE_INFO.

    Attributes:
        Optional[str]:
            ▪ None when no additional info needed
            ▪ Str of specific questions/requirements when status=INCOMPLETE_INFO
            ▪ Should be a clear, actionable prompt for the user


    Example:
        What is the target deployment platform? Are there any latency constraints?
    """

    @property
    def requires_user_input(self) -> bool:
        """Convenience property to check if additional information is needed"""
        return self.status == PlanStatus.INCOMPLETE_INFO and bool(self.information_required)


# Type alias for plan parser functions
PlanParser: TypeAlias = Callable[[str], PlanResult]


class Planner(BaseAgent[PlanResult]):
    """
    Specialized agent for generating and managing research plans.

    Inherits from BaseAgent with PlanResult as the concrete output type.
    Handles the full lifecycle of research plan creation and modification.
    """
    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
            tips_prompt_template: Optional[PromptTemplate] = None,
            plan_parser: PlanParser = None,
            latest_search_plan: Optional[str] = None,
            historical_messages: Optional[List[HistoricalMessage]] = None,
    ) -> None:
        """
        Initialize the Planner agent with configuration and optional dependencies.

        Args:
            model_config: Configuration for the AI model
            mcp_tools_config_path: Path to MCP tools configuration
            mcp_client_timeout: Timeout for MCP client operations
            plan_parser: Custom parser for plan responses (defaults to built-in)
            latest_search_plan: Current search plan context
            historical_messages: List of historical messages
        """
        super().__init__(model_config, mcp_tools_config_path, mcp_client_timeout, tips_prompt_template)
        self.plan_parser = plan_parser or self._default_plan_parser
        # Init plan
        if historical_messages:
            self._init_historical_messages_to_memory(historical_messages)
        self.current_search_plan = None
        latest_search_plan = latest_search_plan or next(
            (msg.content for msg in reversed(historical_messages)
             if msg.type != HistoricalMessageType.USER),
            None
        ) if historical_messages else None
        if latest_search_plan:
            self.current_search_plan = self.plan_parser(latest_search_plan)

    def _init_historical_messages_to_memory(self, historical_messages: List[HistoricalMessage]) -> None:
        for message in historical_messages:
            if message.type == HistoricalMessageType.USER:
                agent_message = BaseMessage.make_user_message(
                    role_name="User",
                    content=message.content,
                )
            else:
                agent_message = BaseMessage.make_assistant_message(
                    role_name="Assistant",
                    content=message.content,
                )
            self.agent.update_memory(
                message=agent_message,
                role=OpenAIBackendRole.USER if message.type == HistoricalMessageType.USER else OpenAIBackendRole.SYSTEM,
                timestamp=message.created_time.timestamp(),
            )

    @override
    def build_system_prompt(self) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(PromptStage.PLAN_SYSTEM)

    @override
    def build_user_prompt(
            self,
            *,
            query: str,
            context: Dict[str, Any] | None = None,
    ) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(
            stage=PromptStage.PLAN_USER, variables=dict(
                query=query,
                current_search_plan=self._search_plan_text(self.current_search_plan) if self.current_search_plan else "",
                **context if context is not None else {},
            )
        )

    @override
    def parse_output(self, response: ChatAgentResponse) -> PlanResult:
        if self.plan_parser is not None:
            return self.plan_parser(response.msg.content)
        return self._default_plan_parser(response.msg.content)

    def _default_plan_parser(self, full_response: str) -> PlanResult:
        """
        Default parser for converting LLM responses to PlanResult objects.

        Handles multiple response formats:
        - Information requests
        - Plan finalization
        - New plan generation

        Args:
            full_response: Complete LLM response string

        Returns:
            PlanResult: Parsed plan information

        Raises:
            NoPlanException: If attempting to finalize without history
        """
        # Need more information
        if not full_response.startswith("开始研究") and (
                '<plan>' not in full_response or '</plan>' not in full_response):
            return PlanResult(
                status=PlanStatus.INCOMPLETE_INFO,
                information_required=full_response
            )

        if full_response.startswith("开始研究"):
            if not self.current_search_plan:
                raise NoPlanException("No plan can not start")
            # If need start research
            final_plan = deepcopy(self.current_search_plan)
            final_plan.status = PlanStatus.FINALIZED
            return final_plan
        else:
            # Has generate or modify a plan
            pattern = r'<plan>(.*?)</plan>'
            try:
                plan_content_search = re.search(pattern, full_response, re.DOTALL)
                if plan_content_search:
                    plan_content = plan_content_search.group(1)
                else:
                    plan_content = full_response
            except Exception as e:
                logging.warning(f"Parse plan error {e}")
                plan_content = full_response
            search_plans = []
            line_steps = plan_content.split("\n")
            for i, step in enumerate(line_steps):
                step = step.strip()
                if step:
                    search_step_title = step
                    search_step_description = ""
                    splitted = re.split(r"[：:]", step, maxsplit=1)  # 匹配中文或英文冒号
                    if len(splitted) == 2:
                        try:
                            search_step_title = re.search(r'\*\*（\d+）(.+?)\*\*', splitted[0]).group(1)
                        except Exception:
                            search_step_title = splitted[0]

                        search_step_description = splitted[1]
                    search_plans.append(
                        SearchPlan(
                            title=search_step_title,
                            description=search_step_description,
                            origin_plan=step
                        )
                    )
            plan_result = PlanResult(
                search_plans=search_plans,
                status=PlanStatus.DRAFT,
            )
            return plan_result

    def post_run(self, output: PlanResult) -> Generator[Message, None, None]:
        """Post run process."""
        self.current_search_plan = output
        if output.status == PlanStatus.FINALIZED:
            yield from self.yield_tips_messages(PromptStage.PLAN_START_TIPS, search_plans=self._search_plan_text(output))
        yield from super().post_run(output)

    def _search_plan_text(self, plan_result: PlanResult) -> str:
        if plan_result.search_plans:
            return "\n".join([each.origin_plan for each in plan_result.search_plans])
        else:
            return ""
