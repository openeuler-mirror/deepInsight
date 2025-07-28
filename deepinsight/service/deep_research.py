# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Generator, Union, List, Dict, Optional

from camel.types import ModelPlatformType, ModelType
from camel.types.agents import ToolCallingRecord
from pydantic import BaseModel

from deepinsight.config.model import ModelConfig
from deepinsight.core.orchestrator import Orchestrator, OrchestrationResult, OrchestratorStatusType, \
    OrchestrationRequest
from deepinsight.core.prompt.prompt_template import PromptStage
from deepinsight.core.types.agent import AgentType, AgentMessageAdditionType
from deepinsight.core.types.historical_message import HistoricalMessage, HistoricalMessageType
from deepinsight.core.types.messages import StartMessage, ChunkMessage, EndMessage, MessageMetadataKey, CompleteMessage, \
    ErrorMessage, Message as CoreMessage
from deepinsight.service.schemas.chat import ChatMessage
from deepinsight.stores.postgresql.database import get_database_session
from deepinsight.stores.postgresql.repositories.conversation_repository import ConversationRepository
from deepinsight.stores.postgresql.repositories.message_repository import MessageRepository
from deepinsight.stores.postgresql.repositories.report_repository import ReportRepository
from deepinsight.stores.postgresql.schemas.message import Message as DBSchemaMessage
from deepinsight.stores.postgresql.schemas.report import Report as DBSchemaReport

AGENT_PROCESS_TIPS_TEMPLATE = {
    PromptStage.PLAN_START_TIPS: "研究计划如下\n{search_plans}",
    PromptStage.RESEARCH_START_TIPS: "任务{task_id}: {task_title}",
    PromptStage.REPORT_PLAN_TIPS: "正在分析结果，计划生成报告",
    PromptStage.REPORT_WRITE_TIPS: "正在生成报告",
}


class ThoughtProcessType(str, Enum):
    TITLE = "title"
    CONTENT = "content"
    TOOL_CALL = "tool_call"


class MessageType(str, Enum):
    USER = "user"
    SEARCH_PLAN = "search_plan"
    REPORT = "report"


class MessageItem(BaseModel):
    type: MessageType
    content: str = ""
    created_at: datetime
    message_id: str


class ThoughtItem(BaseModel):
    type: ThoughtProcessType
    content: Union[str, ToolCallingRecord] = ""
    created_at: datetime


class ReportItem(BaseModel):
    type: MessageType = MessageType.REPORT
    content: str = ""
    created_at: datetime


class ThoughtMessages(BaseModel):
    messages: List[ThoughtItem]


class DeepResearchResponse(BaseModel):
    thought_and_report: Optional[List[Union[ThoughtItem, ReportItem]]] = None
    message: Optional[MessageItem] = None


class ThoughtAndReport(BaseModel):
    thought: ThoughtMessages
    report: ReportItem


class DeepResearchService:
    """
    Stateless service for deep research operations that:
    - Coordinates research orchestration
    - Handles database persistence
    - Manages streaming responses

    Designed for single-use per API request with no shared state.
    """

    @classmethod
    def research(
            cls,
            query: str,
            conversation_id: str,
            user_id: str,
    ) -> Generator[Optional[ChatMessage], None, None]:
        # Initialize repositories with fresh session
        with get_database_session() as db:
            conversation_repo = ConversationRepository(db)
            message_repo = MessageRepository(db)
            report_repo = ReportRepository(db)

            conversation = conversation_repo.get_by_id(conversation_id)
            if not conversation:
                raise ValueError(f"Conversation {conversation_id} not found")

            # Add user query to history
            user_message = DBSchemaMessage(
                conversation_id=conversation_id,
                content=query,
                type=MessageType.USER.value,
            )
            message_repo.create(user_message)

            run_orchestration_generator = cls._run_orchestration(
                query=query,
                conversation=conversation,
                user_id=user_id,
                report_repo=report_repo,
                message_repo=message_repo,
            )
            try:
                while True:
                    item = next(run_orchestration_generator)
                    yield cls._wrap_response(item)
            except StopIteration as e:
                pass

    @classmethod
    def _wrap_response(
            cls,
            original_response: DeepResearchResponse,
    ) -> Optional[ChatMessage]:
        if original_response.thought_and_report and original_response.message:
            return ChatMessage(
                id=original_response.message.message_id,
                content=original_response.thought_and_report,
                role=MessageType.REPORT.value,
                created_at=original_response.message.created_at
            )
        elif original_response.message:
            return ChatMessage(
                id=original_response.message.message_id,
                content=original_response.message.content,
                role=MessageType.SEARCH_PLAN.value,
                created_at=original_response.message.created_at
            )
        return None

    @classmethod
    def get_report_and_thought_by_message_id(cls, message_id: str):
        with get_database_session() as db:
            report_repo = ReportRepository(db)
            report_and_thought_data = report_repo.get_by_message_id(message_id)
            thought_process = ThoughtMessages.model_validate_json(report_and_thought_data.thought)
            report = ReportItem(
                content=report_and_thought_data.report_content,
                created_at=report_and_thought_data.created_time,
            )
            return ThoughtAndReport(thought=thought_process, report=report)

    @classmethod
    def _run_orchestration(
            cls,
            query,
            conversation,
            user_id,
            report_repo: ReportRepository,
            message_repo: MessageRepository,
    ) -> Generator[DeepResearchResponse, None, None]:
        full_response = None
        thought_and_report_process: List[Union[ThoughtItem, ReportItem]] = []
        thought_process = ThoughtMessages(
            messages=[],
        )

        final_report: ReportItem = None

        history_interactive_messages = cls._get_messages_by_conversation_id_until_report(
            conversation_id=conversation.conversation_id,
            message_repo=message_repo,
        )
        orchestration = Orchestrator(
            model_config=ModelConfig(
                model_platform=ModelPlatformType.DEEPSEEK,
                model_type=ModelType.DEEPSEEK_CHAT,
                model_config_dict=dict(
                    stream=True
                ),
            ),
            # mcp_tools_config_path="./mcp_config.json",
            research_round_limit=1,
            init_request=OrchestrationRequest(
                agent_historical_messages={
                    AgentType.PLANNER: [cls._convert_message_to_orchestration_message(each) for each in
                                        history_interactive_messages],
                }
            ),
            execute_tips_template_dict=AGENT_PROCESS_TIPS_TEMPLATE,
        )
        orchestration_generator = orchestration.run(query)
        current_orchestration_phase = OrchestratorStatusType.PENDING
        stream_chunk_caches: Dict[str, Union[MessageItem, ThoughtItem, ReportItem]] = {}
        report_db_item: DBSchemaReport = None
        message_db_item: DBSchemaMessage = None

        try:
            while True:
                item = next(orchestration_generator)
                if isinstance(item, OrchestratorStatusType):
                    current_orchestration_phase = item
                    message_new_db_item = cls._insert_message_by_orchestration_phase(
                        conversation=conversation,
                        message_repo=message_repo,
                        current_orchestration_phase=current_orchestration_phase,
                        message=item,
                    )
                    if message_new_db_item:
                        message_db_item = message_new_db_item
                    report_db_item = cls._insert_or_update_thought_and_report_process(
                        conversation=conversation,
                        current_orchestration_phase=current_orchestration_phase,
                        report_repo=report_repo,
                        relative_message=message_db_item,
                        has_insert_report=report_db_item,
                        message=item,
                        thought_process=thought_process,
                    )
                else:
                    if isinstance(item, StartMessage):
                        stream_id = item.stream_id
                        cached_item = cls._create_precess_item_by_orchestration_phase(
                            current_orchestration_phase, item
                        )
                        stream_chunk_caches[stream_id] = cached_item

                        if cached_item:
                            # Add message to report history
                            if isinstance(cached_item, MessageItem):
                                full_response = DeepResearchResponse(
                                    message=cached_item
                                )
                            else:
                                thought_and_report_process.append(cached_item)
                                full_response = DeepResearchResponse(
                                    message=MessageItem(
                                        message_id=str(message_db_item.message_id),
                                        type=message_db_item.type,
                                        created_at=message_db_item.created_time,
                                    ),
                                    thought_and_report=thought_and_report_process,
                                )

                        if isinstance(cached_item, ReportItem):
                            final_report = cached_item

                    elif isinstance(item, ChunkMessage):
                        stream_id = item.stream_id
                        cached_item = stream_chunk_caches.setdefault(
                            stream_id,
                            cls._create_precess_item_by_orchestration_phase(
                                current_orchestration_phase, item
                            )
                        )
                        cached_item.content += item.payload
                    elif isinstance(item, EndMessage):
                        stream_id = item.stream_id
                        if stream_id not in stream_chunk_caches:
                            cached_item = cls._create_precess_item_by_orchestration_phase(
                                current_orchestration_phase, item
                            )
                        else:
                            cached_item = stream_chunk_caches[stream_id]
                        cached_item.content += item.payload
                        if cached_item.content:
                            message_new_db_item = cls._insert_message_by_orchestration_phase(
                                conversation=conversation,
                                message_repo=message_repo,
                                current_orchestration_phase=current_orchestration_phase,
                                message=cached_item,
                            )
                            if message_new_db_item:
                                message_db_item = message_new_db_item
                            report_db_item = cls._insert_or_update_thought_and_report_process(
                                conversation=conversation,
                                current_orchestration_phase=current_orchestration_phase,
                                report_repo=report_repo,
                                relative_message=message_db_item,
                                has_insert_report=report_db_item,
                                message=cached_item,
                                thought_process=thought_process,
                            )
                        else:
                            if cached_item in thought_and_report_process:
                                thought_and_report_process.remove(cached_item)
                        stream_chunk_caches.pop(stream_id)
                    else:
                        if isinstance(item, CompleteMessage) and not item.payload:
                            continue
                        if isinstance(item, ErrorMessage) and not item.error_message:
                            continue
                        process_item = cls._create_precess_item_by_orchestration_phase(
                            current_orchestration_phase, item
                        )
                        if process_item:
                            message_new_db_item = cls._insert_message_by_orchestration_phase(
                                conversation=conversation,
                                message_repo=message_repo,
                                current_orchestration_phase=current_orchestration_phase,
                                message=process_item,
                            )
                            if message_new_db_item:
                                message_db_item = message_new_db_item
                            if isinstance(process_item, MessageItem):
                                full_response = DeepResearchResponse(
                                    message=process_item
                                )
                            else:
                                thought_and_report_process.append(process_item)
                                full_response = DeepResearchResponse(
                                    message=MessageItem(
                                        message_id=str(message_db_item.message_id),
                                        type=message_db_item.type,
                                        created_at=message_db_item.created_time,
                                    ),
                                    thought_and_report=thought_and_report_process,
                                )
                            report_db_item = cls._insert_or_update_thought_and_report_process(
                                conversation=conversation,
                                current_orchestration_phase=current_orchestration_phase,
                                report_repo=report_repo,
                                relative_message=message_db_item,
                                has_insert_report=report_db_item,
                                message=process_item,
                                thought_process=thought_process,
                            )
                    yield full_response

        except StopIteration as e:
            report_artifact: OrchestrationResult = e.value
            if report_artifact.report:
                final_report = ReportItem(
                    content=report_artifact.report,
                    created_at=datetime.now(),
                )
                thought_and_report_process.append(final_report)
                full_response = DeepResearchResponse(
                    message=MessageItem(
                        message_id=str(message_db_item.message_id),
                        type=message_db_item.type,
                        created_at=message_db_item.created_time,
                    ),
                    thought_and_report=thought_and_report_process,
                )
                report_db_item = cls._insert_or_update_thought_and_report_process(
                    conversation=conversation,
                    current_orchestration_phase=current_orchestration_phase,
                    report_repo=report_repo,
                    relative_message=message_db_item,
                    has_insert_report=report_db_item,
                    message=final_report,
                    thought_process=thought_process,
                )
                yield full_response

    @classmethod
    def _get_messages_by_conversation_id_until_report(cls, conversation_id: str, message_repo: MessageRepository) -> \
            List[DBSchemaMessage]:
        """
        Retrieve messages for a conversation in reverse chronological order,
        stopping when a report message is encountered.

        Args:
            conversation_id: str of the conversation to query
            message_repo: SQLAlchemy session object

        Returns:
            List of Message objects in chronological order (oldest first)
        """
        # Query messages in descending order (newest first)
        messages = message_repo.get_all_by_conversation_id(conversation_id=conversation_id)

        # Collect messages until we hit a report
        filtered_messages = []
        for msg in reversed(messages):  # 从最新消息开始检查
            if msg.type == MessageType.REPORT:
                break
            filtered_messages.append(msg)

        # Return in chronological order (oldest first)
        return list(reversed(filtered_messages))

    @classmethod
    def _convert_message_to_orchestration_message(cls, message: DBSchemaMessage) -> HistoricalMessage:
        return HistoricalMessage(
            content=message.content,
            type=HistoricalMessageType.RESEARCH_PLAN if message.type == "search_plan" else HistoricalMessageType(
                message.type),
            created_time=message.created_time,
            message_id=str(message.message_id)
        )

    @classmethod
    def _insert_message_by_orchestration_phase(
            cls,
            conversation,
            message_repo: MessageRepository,
            current_orchestration_phase: OrchestratorStatusType,
            message: Union[OrchestratorStatusType, MessageItem, ThoughtItem, ReportItem]
    ) -> Optional[DBSchemaMessage]:
        if isinstance(message, OrchestratorStatusType):
            # When receiving a PhaseStartMessage of RESEARCHING phase,
            # insert an empty REPORT type message as a placeholder
            if message == OrchestratorStatusType.RESEARCHING:
                return message_repo.create(
                    DBSchemaMessage(
                        conversation_id=conversation.conversation_id,
                        content="",
                        type=MessageType.REPORT,
                    )
                )
        elif current_orchestration_phase == OrchestratorStatusType.PLANNING:
            # Stores CompleteMessage/ErrorMessage as SEARCH_PLAN during PLANNING phase
            return message_repo.create(
                DBSchemaMessage(
                    conversation_id=conversation.conversation_id,
                    content=message.content,
                    type=MessageType.SEARCH_PLAN,
                )
            )
        logging.warning(f"Orchestration phase {current_orchestration_phase!s} does not require message insertion")
        return None

    @classmethod
    def _insert_or_update_thought_and_report_process(
            cls,
            conversation,
            current_orchestration_phase: OrchestratorStatusType,
            report_repo: ReportRepository,
            relative_message: Optional[DBSchemaMessage],
            has_insert_report: DBSchemaReport,
            message: Optional[Union[OrchestratorStatusType, ThoughtItem, ReportItem]],
            thought_process: ThoughtMessages,
    ):
        if relative_message is None:
            logging.warning(f"Relative message is None, can not insert or update report.")
            return None
        if isinstance(message, OrchestratorStatusType):
            if message == OrchestratorStatusType.RESEARCHING and has_insert_report is None:
                has_insert_report = report_repo.create(
                    DBSchemaReport(
                        message_id=relative_message.message_id,
                        conversation_id=conversation.conversation_id,
                        thought="",
                        report_content="",
                    )
                )
        else:
            if current_orchestration_phase == OrchestratorStatusType.RESEARCHING or current_orchestration_phase == OrchestratorStatusType.REPORT_PLANNING:
                if isinstance(message, ThoughtItem):
                    thought_process.messages.append(message)
                if has_insert_report is not None:
                    has_insert_report.thought = thought_process.model_dump_json()
                    report_repo.update(has_insert_report)
                    return has_insert_report
            elif current_orchestration_phase == OrchestratorStatusType.REPORT_WRITING or current_orchestration_phase == OrchestratorStatusType.COMPLETED:
                if has_insert_report is not None and isinstance(message, ReportItem):
                    has_insert_report.report_content = message.content
                    report_repo.update(has_insert_report)
                    return has_insert_report

        return has_insert_report

    @classmethod
    def _create_precess_item_by_orchestration_phase(
            cls,
            current_orchestration_phase: OrchestratorStatusType,
            data: CoreMessage
    ) -> Optional[Union[MessageItem, ThoughtItem, ReportItem]]:
        if isinstance(data, ErrorMessage):
            return MessageItem(
                type=MessageType.SEARCH_PLAN,
                content=data.error_message,
                created_at=data.timestamp,
                message_id=str(uuid.uuid4()),
            )
        elif current_orchestration_phase == OrchestratorStatusType.PLANNING:
            if isinstance(data.payload, str):
                return MessageItem(
                    type=MessageType.SEARCH_PLAN,
                    content=data.payload,
                    created_at=data.timestamp,
                    message_id=str(uuid.uuid4()),
                )
        elif current_orchestration_phase == OrchestratorStatusType.RESEARCHING or current_orchestration_phase == OrchestratorStatusType.REPORT_PLANNING:
            thought_type = ThoughtProcessType.CONTENT
            if isinstance(data.payload, ToolCallingRecord):
                thought_type = ThoughtProcessType.TOOL_CALL
            elif data.metadata.get(MessageMetadataKey.ADDITION_TYPE,
                                   None) == AgentMessageAdditionType.TIPS:
                thought_type = ThoughtProcessType.TITLE
            return ThoughtItem(
                type=thought_type,
                content=data.payload.model_dump_json() if isinstance(data.payload,
                                                                     ToolCallingRecord) else data.payload,
                created_at=data.timestamp,
            )
        elif current_orchestration_phase == OrchestratorStatusType.REPORT_WRITING:
            return ReportItem(
                content=data.payload,
                created_at=data.timestamp,
            )
        return None
