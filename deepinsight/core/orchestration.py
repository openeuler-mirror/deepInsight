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
from typing import Optional, Any, Generator, List, Union

from pydantic import BaseModel

from deepinsight.config.model import ModelConfig
from deepinsight.core.agent.planner import Planner, PlanResult
from deepinsight.core.agent.reporter import Reporter
from deepinsight.core.agent.researcher import Researcher, ResearchExecution
from deepinsight.core.messages import Message


class OrchestrationPhase(str, Enum):
    """
    Enumeration of possible orchestration phases in the workflow pipeline.

    Tracks the current stage of the orchestration process from start to completion.
    """
    PENDING = "pending"
    PLANNING = "planning"
    RESEARCHING = "researching"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


class OrchestrationException(Exception):
    """
    Custom exception for orchestration pipeline failures.

    Captures:
    - The stage where failure occurred
    - The original exception
    - Timestamp of failure
    """
    def __init__(self, stage: str, original_error: Exception):
        super().__init__(f"Pipeline failed at {stage}: {str(original_error)}")
        self.stage = stage
        self.original_error = original_error
        self.timestamp = datetime.now()


class OrchestrationArtifact(BaseModel):
    """
    Container for final output artifacts from successful orchestration.

    Attributes:
        report: The compiled report output string
    """
    report: str


class PhaseStartMessage(BaseModel):
    """
    Notification message indicating the start of a new phase.

    Attributes:
        phase: The phase that is beginning
    """
    phase: OrchestrationPhase


class Orchestration:
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
    ) -> None:
        """
        Initialize the orchestration engine with configuration.

        Args:
            model_config: Configuration for the AI model
            mcp_tools_config_path: Path to MCP tools configuration
            mcp_client_timeout: Timeout for MCP client operations
            research_round_limit: Maximum number of research iterations
        """
        self.planner = Planner(
            model_config=model_config,
            mcp_tools_config_path=mcp_tools_config_path,
            mcp_client_timeout=mcp_client_timeout,
        )

        self.researcher = Researcher(
            model_config=model_config,
            mcp_tools_config_path=mcp_tools_config_path,
            mcp_client_timeout=mcp_client_timeout,
            round_limit=research_round_limit
        )

        self.reporter = Reporter(
            model_config=model_config,
            mcp_tools_config_path=mcp_tools_config_path,
            mcp_client_timeout=mcp_client_timeout,
        )
        self.current_phase = OrchestrationPhase.PENDING
        self.start_time = None
        self.end_time = None

    def run(
            self,
            query: str
    ) -> Generator[Union[Message, PhaseStartMessage], None, OrchestrationArtifact]:
        """
        Execute the full orchestration workflow for a given query.

        Args:
            query: The input query to process

        Yields:
            Union[Message, PhaseStartMessage]: Progress messages during execution

        Returns:
            OrchestrationArtifact: Final output artifacts

        Raises:
            OrchestrationException: If any phase fails
        """
        # Type hints for better IDE support
        self.current_phase = OrchestrationPhase.PENDING
        self.start_time = datetime.utcnow()

        try:
            # Phase 1: plan
            self.current_phase = OrchestrationPhase.PLANNING
            yield PhaseStartMessage(phase=self.current_phase)

            plan_result = yield from self.planner.run(query)

            # Phase 2: research
            self.current_phase = OrchestrationPhase.RESEARCHING
            yield PhaseStartMessage(phase=self.current_phase)

            research_executions = yield from self.researcher.run(
                query=query,
                plan_result=plan_result
            )

            # Phase 3: report
            self.current_phase = OrchestrationPhase.REPORTING
            yield PhaseStartMessage(phase=self.current_phase)

            report_result = yield from self.reporter.run(
                query=query,
                research_executions=research_executions,
            )

            self.current_phase = OrchestrationPhase.COMPLETED
            return OrchestrationArtifact(report=report_result)

        except Exception as exc:
            self.current_phase = OrchestrationPhase.FAILED
            raise OrchestrationException(
                stage=self.current_phase,
                original_error=exc
            ) from exc
        finally:
            self.end_time = datetime.utcnow()
