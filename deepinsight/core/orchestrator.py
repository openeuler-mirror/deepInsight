# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from enum import Enum
from datetime import datetime
from typing import Optional, Generator, List, Union, Dict

from pydantic import BaseModel, Field

from deepinsight.config.model import ModelConfig
from deepinsight.core.agent.planner import Planner, PlanStatus
from deepinsight.core.agent.reporter import Reporter
from deepinsight.core.agent.researcher import Researcher
from deepinsight.core.prompt.prompt_template import PromptTemplate, PromptStage
from deepinsight.core.types.agent import AgentType, AgentExecutePhase
from deepinsight.core.types.historical_message import HistoricalMessage
from deepinsight.core.types.messages import Message, MessageMetadataKey


class OrchestratorStatus(BaseModel):
    """
    Notification message indicating the start of a new phase.

    Attributes:
        status: The status string
    """
    status: str = Field(..., description="Status of the Orchestrator")


class OrchestratorStatusType(str, Enum):
    """
    Enumeration of possible status in the Orchestration.

    Tracks the current stage of the orchestration process from start to completion.

    """
    PENDING = OrchestratorStatus(status="pending")
    PLANNING = OrchestratorStatus(status="planning")
    RESEARCHING = OrchestratorStatus(status="researching")
    REPORT_PLANNING = OrchestratorStatus(status="report_planning")
    REPORT_WRITING = OrchestratorStatus(status="report_writing")
    COMPLETED = OrchestratorStatus(status="completed")
    FAILED = OrchestratorStatus(status="failed")


class OrchestrationException(Exception):
    """
    Custom exception for orchestration pipeline failures.

    Captures:
    - The stage where failure occurred
    - The original exception
    - Timestamp of failure
    """

    def __init__(self, stage: str, original_error: Exception):
        super().__init__(f"Orchestration failed at {stage}: {str(original_error)}")
        self.stage = stage
        self.original_error = original_error
        self.timestamp = datetime.now()


class OrchestrationRequest(BaseModel):
    """
    Request body for Orchestration with agent parameters and historical context

    Attributes:
        agent_historical_messages: Map of previous messages in the conversation,
        key is agent type, value is current histories messages
    """
    agent_historical_messages: Optional[Dict[AgentType, List[HistoricalMessage]]] = Field(
        default=None,
        description="The historical messages received from the agent",
    )


class OrchestrationResult(BaseModel):
    """
    Container for orchestration process outputs with interactive capabilities.

    This model represents the output of a deep research process, supporting both
    final reports and intermediate states requiring user interaction.

    Attributes:
        report: The current research report content. May be partial when waiting
                for user input.
        require_user_interactive: Flag indicating whether the process requires
                                user interaction to proceed.
        require_user_feedback: Specific prompt/question to present to the user when
                             interaction is required. Contains None when no feedback
                             is needed.
    """
    report: Optional[str] = Field(default=None, description="Current report content")
    plan_draft: Optional[str] = Field(default=None, description="Plan draft content")
    require_user_interactive: bool = Field(
        default=False,
        description="Boolean flag indicating if the orchestration process is "
                    "currently blocked waiting for user input. True when user "
                    "feedback is required to proceed with the research."
    )
    require_user_feedback: Optional[str] = Field(
        default=None,
        description="When require_user_interactive is True, this field contains "
                    "the specific question or prompt to present to the user. "
                    "Format is plain text suitable for direct display in UI. "
                    "None indicates no feedback is currently required."
    )


class Orchestrator:
    """
    Main orchestration engine that coordinates the research workflow.

    Manages the sequential execution of:
    1. Planning phase
    2. Research phase
    3. Reporting phase

    Provides progress updates via message streaming.
    """

    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
            research_round_limit: int = 5,
            init_request: Optional[OrchestrationRequest] = None,
            execute_tips_template_dict: Optional[Dict[Union[str, PromptStage], str]] = None,
    ) -> None:
        """
        Initialize the orchestration engine with configuration.

        Args:
            model_config: Configuration for the AI model
            mcp_tools_config_path: Path to MCP tools configuration
            mcp_client_timeout: Timeout for MCP client operations
            research_round_limit: Maximum number of research iterations
            init_request: Init request for orchestration
        """
        self.agent_execute_phase_tips_template = self._init_tips_template(execute_tips_template_dict)
        self.planner = Planner(
            model_config=model_config,
            mcp_tools_config_path=mcp_tools_config_path,
            mcp_client_timeout=mcp_client_timeout,
            tips_prompt_template=self.agent_execute_phase_tips_template,
            historical_messages=init_request.agent_historical_messages.get(
                AgentType.PLANNER, []
            ) if init_request else []
        )

        self.researcher = Researcher(
            model_config=model_config,
            mcp_tools_config_path=mcp_tools_config_path,
            mcp_client_timeout=mcp_client_timeout,
            tips_prompt_template=self.agent_execute_phase_tips_template,
            round_limit=research_round_limit
        )

        self.reporter = Reporter(
            model_config=model_config,
            mcp_tools_config_path=mcp_tools_config_path,
            mcp_client_timeout=mcp_client_timeout,
            tips_prompt_template=self.agent_execute_phase_tips_template,
        )
        self.current_phase = OrchestratorStatusType.PENDING
        self.start_time = None
        self.end_time = None

    def run(
            self,
            query: str
    ) -> Generator[Union[Message, OrchestratorStatusType], None, OrchestrationResult]:
        """
        Execute the full orchestration workflow for a given query.

        Args:
            query: The input query to process

        Yields:
            Union[Message, PhaseStartMessage]: Progress messages during execution

        Returns:
            OrchestrationResult: Final output artifacts

        Raises:
            OrchestrationException: If any phase fails
        """
        # Type hints for better IDE support
        self.current_phase = OrchestratorStatusType.PENDING
        self.start_time = datetime.utcnow()

        try:
            # Phase 1: plan
            self.current_phase = OrchestratorStatusType.PLANNING
            yield OrchestratorStatusType.PLANNING

            plan_result = yield from self.planner.run(query)
            if plan_result.requires_user_input:
                # Need user feedback
                return OrchestrationResult(
                    require_user_interactive=True,
                    require_user_feedback=plan_result.information_required,
                )
            elif plan_result.status == PlanStatus.DRAFT:
                # Need user confirm plan draft
                return OrchestrationResult(
                    require_user_interactive=True,
                    plan_draft="\n".join([plan.origin_plan for plan in plan_result.search_plans])
                )

            # Phase 2: research
            self.current_phase = OrchestratorStatusType.RESEARCHING
            yield OrchestratorStatusType.RESEARCHING

            research_executions = yield from self.researcher.run(
                query=query,
                plan_result=plan_result
            )

            # Phase 3: report

            self.current_phase = OrchestratorStatusType.REPORT_PLANNING
            yield OrchestratorStatusType.REPORT_PLANNING

            reporter_run_generator = self.reporter.run(
                query=query,
                research_executions=research_executions,
            )
            try:
                while True:
                    item = next(reporter_run_generator)
                    if item.metadata.get(MessageMetadataKey.AGENT_EXECUTE_PHASE, None) == AgentExecutePhase.REPORT_WRITING:
                        self.current_phase = OrchestratorStatusType.REPORT_WRITING
                        yield OrchestratorStatusType.REPORT_WRITING
                    else:
                        yield item
            except StopIteration as e:
                report_result = e.value

            self.current_phase = OrchestratorStatusType.COMPLETED
            return OrchestrationResult(report=report_result)

        except Exception as exc:
            self.current_phase = OrchestratorStatusType.FAILED
            raise OrchestrationException(
                stage=self.current_phase,
                original_error=exc
            ) from exc
        finally:
            self.end_time = datetime.utcnow()


    def _init_tips_template(self, execute_tips_template_dict: Dict):
        tips_template = PromptTemplate(
            template_dict={}
        )
        if execute_tips_template_dict:
            for key, value in execute_tips_template_dict.items():
                tips_template.add_template(key, value)
        return tips_template
