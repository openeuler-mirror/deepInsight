from __future__ import annotations

import asyncio
import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk, ToolMessage, ToolMessageChunk
from langgraph.types import StateSnapshot, Interrupt, Command
from langgraph.graph.state import CompiledStateGraph

from deepinsight.service.schemas.streaming import (
    EventType,
    Message as ResponseMessage,
    MessageContent as ResponseMessageContent,
    MessageContentType as ResponseMessageContentType,
    MessageToolCallContent,
    StreamEvent,
    Message,
    MessageContentType,
)

from deepinsight.core.types import (
    FinalResult,
    ToolUnifiedResponse,
    ClarifyNeedUser,
    WaitResearchBriefEdit,
    WaitReportOutlineEdit,
    DeepResearchNodeName,
)

class StreamEventAdapter:
    """Adapt LangGraph/LangChain streaming output to unified StreamEvent.

    This adapter consumes an async iterator of raw chunks (from graph `astream`)
    and emits `StreamEvent` instances conforming to `deepinsight.service.schemas.streaming`.

    It is intentionally decoupled from specific graph implementations. Scenario-specific
    differences should be injected via constructor parameters.
    """

    def __init__(
        self,
        text_stream_block_nodes: Optional[Iterable[str]] = None,
        tool_call_stream_block_nodes: Optional[Iterable[str]] = None,
        blocked_tool_names: Optional[Iterable[str]] = None,
    ) -> None:
        """
        Initialize stream adapter with suppression rules.

        - text_stream_block_nodes: Node names whose TEXT chunks should be suppressed from streaming.
        - tool_call_stream_block_nodes: Node names whose TOOL CALL results should be suppressed from streaming.
        - blocked_tool_names: Tool names to suppress even if the above node-level suppression doesn't apply.
        """
        self.text_stream_block_nodes = set(text_stream_block_nodes or [])
        self.tool_call_stream_block_nodes = set(tool_call_stream_block_nodes or [])
        self.blocked_tool_names = set(blocked_tool_names or [])

    def _convert_messages_to_langchain(self, messages: List[Message]) -> List[Any]:
        """Convert List[Message] to List[BaseMessage] for LangChain."""
        langchain_messages = []
        for msg in messages:
            if msg.content_type == MessageContentType.plain_text and msg.content.text:
                langchain_messages.append(HumanMessage(content=msg.content.text))
            elif msg.content_type == MessageContentType.tool_call and msg.content.tool_calls:
                # For tool calls, we might need to create ToolMessage or handle differently
                # For now, we'll extract text if available or skip
                for tool_call in msg.content.tool_calls:
                    if tool_call.result:
                        # If there's a result, create a ToolMessage
                        tool_content = json.dumps(tool_call.result) if isinstance(tool_call.result, dict) else str(tool_call.result)
                        langchain_messages.append(
                            ToolMessage(
                                content=tool_content,
                                tool_call_id=tool_call.id or "",
                                name=tool_call.name or "",
                            )
                        )
        return langchain_messages

    async def run_graph(
        self,
        graph: CompiledStateGraph,
        messages: List[Message],
        graph_config: Optional[RunnableConfig] = None,
        stream_modes: Optional[List[str]] = None,
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run a graph and yield unified `StreamEvent`s.

        Parameters
        - graph: a LangGraph/LangChain graph-like object exposing `astream(...)`
        - messages: list of messages in the conversation
        - graph_config: configuration dict passed into graph execution
        - stream_modes: modes requested from graph, e.g. ["messages", "custom", "updates"]
        """
        graph_config = graph_config or {}
        stream_modes = stream_modes or ["messages", "custom", "updates"]
        tool_call_accumulator = {}
        
        # Validate messages
        if not messages:
            raise ValueError("Messages list cannot be empty")
        
        # Convert Message to LangChain BaseMessage
        langchain_messages = self._convert_messages_to_langchain(messages)
        if not langchain_messages:
            raise ValueError("No valid messages could be converted from the input messages list")
        
        init_state = {
            "messages": langchain_messages,
        }
        state: StateSnapshot = graph.get_state(config=graph_config)

        # Resolve conversation id from function arg first, fallback to graph_config
        resolved_conversation_id = conversation_id or graph_config.get("conversation_id")

        # Extract the last plain text message for resume command if needed
        resume_content = ""
        for msg in reversed(messages):
            if msg.content_type == MessageContentType.plain_text and msg.content.text:
                resume_content = msg.content.text
                break

        # Call the underlying graph's streaming API
        if not state.interrupts:
            async for namespace, mode, data in graph.astream(
                    init_state,
                    config=graph_config,
                    subgraphs=True,
                    stream_mode=stream_modes,
            ):
                async for stream_event in self.process_graph_stream(
                        namespace=namespace,
                        mode=mode,
                        data=data,
                        run_id=str(graph_config["run_id"]),
                        conversation_id=resolved_conversation_id,
                        tool_call_accumulator=tool_call_accumulator,
                ):
                    yield stream_event
        else:
            async for namespace, mode, data in graph.astream(
                    Command(
                        resume=resume_content,
                    ),
                    config=graph_config,
                    subgraphs=True,
                    stream_mode=stream_modes,
            ):
                async for stream_event in self.process_graph_stream(
                        namespace=namespace,
                        mode=mode,
                        data=data,
                        run_id=str(graph_config["run_id"]),
                        conversation_id=resolved_conversation_id,
                        tool_call_accumulator=tool_call_accumulator,
                ):
                    yield stream_event

    async def process_graph_stream(
            self,
            namespace,
            mode,
            data,
            run_id: str,
            conversation_id: str,
            tool_call_accumulator: Dict[str, List[MessageToolCallContent]]
    ) -> AsyncGenerator[
        StreamEvent, None]:
        if mode == "messages":
            message_chunk, metadata = data

            if isinstance(message_chunk, (AIMessageChunk, AIMessage)):
                async for item in self._process_ai_messages(
                    message_chunk=message_chunk,
                    metadata=metadata,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    tool_call_accumulator=tool_call_accumulator
                ):
                    yield item

            elif isinstance(message_chunk, ToolMessage) and not self._should_filter_tool_call_stream_event(metadata):
                if message_chunk.name not in self.blocked_tool_names:
                    try:
                        parsed = json.loads(message_chunk.content)
                    except Exception as e:
                        # logging.error(f"Failed to parse ToolMessage: {e}, raw={message_chunk.content}")
                        parsed = {"raw": message_chunk.content}

                    yield StreamEvent(
                        event=EventType.thinking_tool_calls_result,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        messages=[
                            ResponseMessage(
                                id=message_chunk.id,
                                parent_message_id=metadata.get("parent_message_id", None),
                                content=ResponseMessageContent(
                                    tool_calls=[MessageToolCallContent(
                                        id=message_chunk.tool_call_id,
                                        name=message_chunk.name,
                                        result=parsed,
                                    )]
                                ),
                                content_type=ResponseMessageContentType.tool_call,
                            )
                        ],
                    )

            else:
                logging.debug(f"Received non-AIMessageChunk {type(message_chunk)}: {message_chunk}")

        elif mode == "custom":
            message_chunk = data
            if isinstance(message_chunk, FinalResult):
                result = message_chunk.final_report

                if message_chunk.expert_review_comments:
                    markdown_parts = []
                    for expert_name, comment in message_chunk.expert_review_comments.items():
                        markdown_parts.append(f"### {expert_name}\n\n{comment.strip()}\n")

                    result += "\n\n" + "\n\n".join(markdown_parts)

                yield StreamEvent(
                    event=EventType.final_report,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    messages=[
                        ResponseMessage(
                            content=ResponseMessageContent(text=result),
                            content_type=ResponseMessageContentType.plain_text,
                        )
                    ],
                )
            elif isinstance(message_chunk, ToolUnifiedResponse):
                yield StreamEvent(
                    event=EventType.thinking_tool_calls_result,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    messages=[
                        ResponseMessage(
                            parent_message_id=message_chunk.parent_message_id,
                            content=ResponseMessageContent(
                                tool_calls=[
                                    MessageToolCallContent(
                                        id=message_chunk.id,
                                        name=message_chunk.name,
                                        args=message_chunk.args,
                                        result=message_chunk.result,
                                    )
                                ]
                            ),
                            content_type=ResponseMessageContentType.tool_call,
                        )
                    ],
                )

        elif mode == "updates":
            if isinstance(data, dict) and "__interrupt__" in data:
                interrupts = data["__interrupt__"]
                if isinstance(interrupts, tuple):
                    for each in interrupts:
                        if not isinstance(each, Interrupt):
                            continue
                        if isinstance(each.value, ClarifyNeedUser):
                            event_type = EventType.interrupt_clarification
                            value = each.value.question
                        elif isinstance(each.value, WaitResearchBriefEdit):
                            event_type = EventType.interrupt_execute_plan_edit
                            value = each.value.research_brief
                        elif isinstance(each.value, WaitReportOutlineEdit):
                            event_type = EventType.interrupt_report_outline_edit
                            value = each.value.report_outline
                        else:
                            event_type = EventType.interrupt
                            value = each.value
                        yield StreamEvent(
                            event=event_type,
                            run_id=run_id,
                            conversation_id=conversation_id,
                            messages=[
                                ResponseMessage(
                                    content=ResponseMessageContent(text=value),
                                    content_type=ResponseMessageContentType.plain_text,
                                )
                            ],
                        )

        else:
            logging.warning(f"Not supported mode {mode}")

    async def _process_ai_messages(
            self,
            message_chunk,
            metadata: dict,
            run_id: str,
            conversation_id: str,
            tool_call_accumulator
    ):
        message_id = message_chunk.id
        message_content = str(message_chunk.content)
        if getattr(message_chunk, "tool_calls", None) or getattr(message_chunk, "tool_call_chunks", None):
            if self._should_filter_tool_call_stream_event(metadata):
                return
            if message_id not in tool_call_accumulator:
                tool_call_accumulator[message_id] = []
            if not isinstance(message_chunk, AIMessage):
                if message_chunk.tool_calls:
                    processed_tool_calls = self._filter_tool_calls(
                        message_id=message_id,
                        tool_calls=message_chunk.tool_calls,
                        tool_call_accumulator=tool_call_accumulator,
                    )
                    if processed_tool_calls:
                        yield StreamEvent(
                            event=EventType.thinking_tool_calls,
                            run_id=run_id,
                            conversation_id=conversation_id,
                            messages=[
                                ResponseMessage(
                                    id=message_id,
                                    parent_message_id=metadata.get("parent_message_id", None),
                                    content=ResponseMessageContent(
                                        tool_calls=processed_tool_calls
                                    ),
                                    content_type=ResponseMessageContentType.tool_call,
                                )
                            ],
                        )
                    conduct_text_messages = self._process_conduct_tool_call_message(
                        message_id=message_id,
                        tool_calls=message_chunk.tool_calls,
                        tool_call_accumulator=tool_call_accumulator,
                        metadata=metadata,
                    )
                    if conduct_text_messages:
                        yield StreamEvent(
                            event=EventType.thinking_step_topic,
                            run_id=run_id,
                            conversation_id=conversation_id,
                            messages=conduct_text_messages,
                        )
            else:
                if message_chunk.tool_call_chunks:
                    processed_tool_calls = self._filter_tool_calls(
                        message_id=message_id,
                        tool_calls=message_chunk.tool_call_chunks,
                        tool_call_accumulator=tool_call_accumulator,
                    )
                    if processed_tool_calls:
                        yield StreamEvent(
                            event=EventType.thinking_tool_calls,
                            run_id=run_id,
                            conversation_id=conversation_id,
                            messages=[
                                ResponseMessage(
                                    id=message_id,
                                    parent_message_id=metadata.get("parent_message_id", None),
                                    content=ResponseMessageContent(
                                        tool_calls=processed_tool_calls
                                    ),
                                    content_type=ResponseMessageContentType.tool_call,
                                )
                            ],
                        )
                    conduct_text_messages = self._process_conduct_tool_call_message(
                        message_id=message_id,
                        tool_calls=message_chunk.tool_call_chunks,
                        tool_call_accumulator=tool_call_accumulator,
                        metadata=metadata,
                    )
                    if conduct_text_messages:
                        yield StreamEvent(
                            event=EventType.thinking_step_topic,
                            run_id=run_id,
                            conversation_id=conversation_id,
                            messages=conduct_text_messages,
                        )

        else:
            if self._should_filter_text_stream_event(metadata):
                return
            yield StreamEvent(
                event=self._get_mesage_chunk_event_type(metadata),
                run_id=run_id,
                conversation_id=conversation_id,
                messages=[
                    ResponseMessage(
                        id=message_id,
                        parent_message_id=metadata.get("parent_message_id", None),
                        content=ResponseMessageContent(text=message_content),
                        content_type=ResponseMessageContentType.plain_text,
                    )
                ],
            )

    def _filter_tool_calls(self, message_id, tool_calls, tool_call_accumulator):
        tool_calls_message = []
        for each in tool_calls:
            index = each["index"]
            while len(tool_call_accumulator[message_id]) <= each["index"]:
                tool_call_accumulator[message_id].append(
                    MessageToolCallContent(
                        id="",
                        name="",
                        args="",
                        result="",
                    )
                )
            acc_call = tool_call_accumulator[message_id][index]
            acc_call.id += each["id"] or ""
            acc_call.name += each["name"] or ""
            acc_call.args += each["args"] or ""
            if acc_call.name not in self.blocked_tool_names:
                tool_calls_message.append(MessageToolCallContent(
                    index=each["index"],
                    id=each["id"],
                    name=each["name"],
                    args=each["args"],
                ))
        return tool_calls_message

    def _process_conduct_tool_call_message(
        self,
        message_id,
        tool_calls,
        tool_call_accumulator,
        metadata,
    ):
        conduct_text_message = []
        for each in tool_calls:
            index = each["index"]
            acc_call = tool_call_accumulator[message_id][index]
            if acc_call.name == "ConductResearch":
                args_object = {}
                try:
                    args_object = json.loads(acc_call.args)
                except Exception:
                    pass
                conduct_text_message.append(ResponseMessage(
                    id=acc_call.id,
                    parent_message_id=metadata.get("parent_message_id", None),
                    content=ResponseMessageContent(
                        text=args_object.get("research_topic", "")
                    ),
                    content_type=ResponseMessageContentType.plain_text,
                ))
        return conduct_text_message

    def _get_mesage_chunk_event_type(self, metadata: dict) -> EventType.thinking_message_chunk:
        node_name = metadata.get("langgraph_node")
        if node_name is None:
            return EventType.thinking_message_chunk
        if node_name == DeepResearchNodeName.GENERATE_REPORT_OUTLINE.value:
            return EventType.thinking_report_outline_generating
        if node_name == DeepResearchNodeName.GENERATE_REPORT.value:
            return EventType.report_chunk
        return EventType.thinking_message_chunk
    
    def _should_filter_text_stream_event(self, metadata: dict) -> bool:
        node_name = metadata.get("langgraph_node")
        if node_name is None:
            return False
        if "." in node_name:
            node_name = node_name.split(".", 1)[0]
        return node_name in self.text_stream_block_nodes

    def _should_filter_tool_call_stream_event(self, metadata: dict) -> bool:
        node_name = metadata.get("langgraph_node")
        if node_name is None:
            return False
        if "." in node_name:
            node_name = node_name.split(".", 1)[0]
        return node_name in self.tool_call_stream_block_nodes
    