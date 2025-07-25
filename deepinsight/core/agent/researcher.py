# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import uuid
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Union, Any, TypeAlias, Callable, Generator, Tuple

from camel.responses import ChatAgentResponse
from camel.types.agents import ToolCallingRecord
from pydantic import BaseModel, Field

from deepinsight.config.model import ModelConfig
from deepinsight.core.agent.base_agent import BaseAgent
from deepinsight.core.agent.planner import SearchPlan, PlanResult
from deepinsight.core.messages import Message
from deepinsight.core.prompt.prompt_template import GLOBAL_DEFAULT_PROMPT_REPOSITORY, PromptStage
from deepinsight.utils.parallel_worker_utils import Executor


class ExecutionStatus(str, Enum):
    """Enum defining possible states of a research execution."""
    PENDING = "pending"  # Execution has been created but not started
    RUNNING = "running"  # Actively being processed
    COMPLETED = "completed"  # Successfully finished
    FAILED = "failed"  # Terminated due to errors
    PAUSED = "paused"  # Temporarily suspended
    CANCELLED = "cancelled"  # Manually terminated


class ExecutionStep(BaseModel):
    """Atomic unit of work in research execution.

    Attributes:
        content (Union[str, Dict, List]): Raw content/input/output
        timestamp (datetime): Creation timestamp
        tool_calls (List[ToolCallRecord]): Associated tool invocations
        metadata (Dict[str, Any]): Additional contextual data
    """
    content: Union[None, str, Dict, List]
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_calls: Optional[List[ToolCallingRecord]] = None
    metadata: Dict[str, Any] = {}


class ResearchExecution(BaseModel):
    """Complete record of a research plan execution.

    Attributes:
        execution_id (str): Unique identifier (auto-generated)
        plan (SearchPlan): Original search plan being executed
        steps (List[ExecutionStep]): Chronological execution trace
        current_status (ExecutionStatus): Current state
        start_time (datetime): Execution start timestamp
        end_time (Optional[datetime]): Completion timestamp
        metrics (Dict[str, Union[float, int]]): Performance measurements
    """
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan: SearchPlan
    steps: List[ExecutionStep] = []
    current_status: ExecutionStatus = ExecutionStatus.PENDING
    start_time: datetime = Field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    metrics: Dict[str, Union[float, int]] = Field(default_factory=dict)

    def add_step(self, step: ExecutionStep) -> None:
        """Append new execution step and update status."""
        self.steps.append(step)
        if self.current_status == ExecutionStatus.PENDING:
            self.current_status = ExecutionStatus.RUNNING

    def fail(self, error: str) -> None:
        """Mark execution as failed with error."""
        self.steps.append(ExecutionStep(
            content={"error": error},
            metadata={"error_type": "execution_failure"}
        ))
        self.current_status = ExecutionStatus.FAILED
        self.end_time = datetime.now()


# Type alias for plan parser functions
ResearchShouldTerminateCallback: TypeAlias = Callable[[ExecutionStep], bool]


class RolePlayingUser(BaseAgent[ChatAgentResponse]):
    """
    Agent representing the user role in a role-playing research scenario.

    Specializes BaseAgent to handle user-side interactions in research dialogues.
    """
    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None
    ) -> None:
        super().__init__(model_config, mcp_tools_config_path, mcp_client_timeout)

    def build_system_prompt(self) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(PromptStage.RESEARCH_ROLE_PLAYING_USER_SYSTEM)

    def build_user_prompt(self, *, query:str, context: Dict[str, Any] | None = None) -> str:
        return query


class RolePlayingAssistant(BaseAgent[ChatAgentResponse]):
    """
    Agent representing the assistant role in a role-playing research scenario.

    Specializes BaseAgent to handle assistant-side interactions in research dialogues.
    """
    def __init__(self, model_config: ModelConfig, mcp_tools_config_path: Optional[str] = None,
                 mcp_client_timeout: Optional[int] = None) -> None:
        super().__init__(model_config, mcp_tools_config_path, mcp_client_timeout)

    def build_system_prompt(self) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(PromptStage.RESEARCH_ROLE_PLAYING_ASSISTANT_SYSTEM)


    def build_user_prompt(self, *, query:str, context: Dict[str, Any] | None = None) -> str:
        return query


class StreamRolePlaying:
    """
    Orchestrates a streaming role-playing dialogue between user and assistant agents.

    Manages the full interaction lifecycle including:
    - Initialization of both role agents
    - Message exchange
    - Termination conditions
    """
    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
            should_terminate_callback: Optional[ResearchShouldTerminateCallback] = None,
            **kwargs,
        ) -> None:
        """
        Initialize the role-playing orchestrator.

        Args:
            model_config: Configuration for the AI model
            mcp_tools_config_path: Path to MCP tools configuration
            mcp_client_timeout: Timeout for MCP client operations
            should_terminate_callback: Optional callback for early termination
            **kwargs: Additional keyword arguments
        """
        super().__init__(**kwargs)
        self.model_config = model_config
        self.mcp_tools_config_path = mcp_tools_config_path
        self.mcp_client_timeout = mcp_client_timeout
        self.should_terminate_callback = should_terminate_callback
        self._init_agent()

    def _init_agent(self) -> None:
        """Initialize both user and assistant role agents."""
        self.user_agent = RolePlayingUser(
            self.model_config,
            self.mcp_tools_config_path,
            self.mcp_client_timeout
        )
        self.assistant_agent = RolePlayingAssistant(
            self.model_config,
            self.mcp_tools_config_path,
            self.mcp_client_timeout
        )

    def run(
            self,
            query: str,
            context: Dict[str, Any] | None = None,
        ) -> Generator[Message, None, Tuple[ChatAgentResponse, ChatAgentResponse]]:
        """
        Execute a complete role-playing dialogue turn.

        Args:
            query: The initial research query
            context: Optional additional context

        Yields:
            Message: Streaming messages during execution

        Returns:
            Tuple containing:
            - Assistant response
            - User response

        Note:
            Handles termination conditions via callback
            Returns empty responses if terminated early
        """
        user_response: ChatAgentResponse = yield from self.user_agent.run(
            query=query,
            context=context,
        )
        if user_response.terminated or user_response.msgs is None or self.should_terminate_callback(
                ExecutionStep(
                    content=user_response.msg.content
                )
        ):
            return (
                ChatAgentResponse(msgs=[], terminated=False, info={}),
                ChatAgentResponse(
                    msgs=[],
                    terminated=user_response.terminated,
                    info=user_response.info,
                ),
            )

        user_msg = user_response.msg
        assistant_response: ChatAgentResponse = yield from self.assistant_agent.run(query=user_msg.content, context=context)
        if assistant_response.terminated or assistant_response.msgs is None:
            return (
                ChatAgentResponse(
                    msgs=[],
                    terminated=assistant_response.terminated,
                    info=assistant_response.info,
                ),
                ChatAgentResponse(
                    msgs=[user_msg], terminated=False, info=user_response.info
                ),
            )

        assistant_msg = assistant_response.msg
        return (
            ChatAgentResponse(
                msgs=[assistant_msg],
                terminated=assistant_response.terminated,
                info=assistant_response.info,
            ),
            ChatAgentResponse(
                msgs=[user_msg],
                terminated=user_response.terminated,
                info=user_response.info,
            ),
        )


class Researcher:
    """
    Coordinates parallel research execution using role-playing agents.

    Manages:
    - Parallel execution of research plans
    - Role-playing dialogues for information gathering
    - Termination conditions
    """

    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
            round_limit: int = 15,
            should_terminate_callback: ResearchShouldTerminateCallback = None,
    ) -> None:
        """
          Initialize the research coordinator.

          Args:
              model_config: Configuration for the AI model
              mcp_tools_config_path: Path to MCP tools configuration
              mcp_client_timeout: Timeout for MCP client operations
              round_limit: Maximum number of dialogue rounds
              should_terminate_callback: Callback for early termination
          """
        self.model_config = model_config
        self.mcp_tools_config_path = mcp_tools_config_path
        self.mcp_client_timeout = mcp_client_timeout
        self.round_limit = round_limit
        self.should_terminate_callback: ResearchShouldTerminateCallback = should_terminate_callback or self._default_should_terminate_callback

    def run(self, query: str, plan_result: PlanResult) -> Generator[Message, None, List[ResearchExecution]]:
        """
        Execute parallel research based on a plan.

        Args:
            query: The research question
            plan_result: Structured research plan

        Yields:
            Message: Streaming messages during execution

        Returns:
            List[ResearchExecution]: Completed research executions
        """
        # Parallel search info
        search_parallel_executor = Executor("Search")
        def search_info_worker(i, search_step: SearchPlan):
            one_search_content = yield from self._search_info_with_role_playing(
                query=query,
                search_plan=search_step,
            )
            return one_search_content or []
        all_content = yield from search_parallel_executor(search_info_worker,
                                                          list(enumerate(plan_result.search_plans)))
        flattened = []
        for i, content in enumerate(all_content):
            flattened.append(content)
        return flattened

    def _default_should_terminate_callback(self, execution_step: ExecutionStep):
        """
        Default termination condition checker.

        Args:
            execution_step: Current execution state

        Returns:
            bool: True if "TASK_DONE" is in the content
        """
        return "TASK_DONE" in execution_step.content

    def _search_info_with_role_playing(
            self, query: str, search_plan: SearchPlan
    ) -> Generator[Message, None, ResearchExecution]:
        """
        Execute a single research dialogue using role-playing.

        Args:
            query: The research question
            search_plan: Specific search plan to execute

        Yields:
            Message: Streaming messages during execution

        Returns:
            ResearchExecution: Completed research execution record
        """
        research_result = ResearchExecution(
            plan=search_plan
        )
        search_role_playing = StreamRolePlaying(
            model_config=self.model_config,
            mcp_tools_config_path=self.mcp_tools_config_path,
            mcp_client_timeout=self.mcp_client_timeout,
            should_terminate_callback=self.should_terminate_callback,
        )
        GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(
            stage=PromptStage.RESEARCH_ROLE_PLAYING_USER_USER,
            variables=dict(
                query=query,
               current_plan=search_plan.origin_plan,
            )
        )

        # Research use role playing
        for _round in range(self.round_limit):
            role_playing_step_generator = search_role_playing.run(query=query)
            try:
                while True:
                    each = next(role_playing_step_generator)
                    yield each
            except StopIteration as e:
                assistant_response, user_response = e.value
            assistant_response: ChatAgentResponse
            user_response: ChatAgentResponse
            last_user_response = user_response
            assistant_execution_step = ExecutionStep(
                content=assistant_response.msg.content,
                tool_calls=assistant_response.info.get("tool_calls") if assistant_response.info.get("tool_calls") else None
            )

            user_execution_stop = ExecutionStep(
                content=user_response.msg.content,
                tool_calls=user_response.info.get("tool_calls") if user_response.info.get("tool_calls") else None
            )

            research_result.add_step(
                user_execution_stop
            )

            research_result.add_step(
                assistant_execution_step
            )

            if (
                    last_user_response.terminated
                    or self.should_terminate_callback(user_execution_stop)
            ):
                break
            query = last_user_response.msg

        return research_result
