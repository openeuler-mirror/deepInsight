from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class MessageContentType(str, Enum):
    plain_text = "plain_text"
    tool_call = "tool_call"


class EventType(str, Enum):
    # normal event
    message_chunk = "message_chunk"

    # interrupt event
    interrupt = "interrupt"
    interrupt_clarification = "interrupt_clarification"
    interrupt_execute_plan_edit = "interrupt_execute_plan_edit"
    interrupt_report_outline_edit = "interrupt_report_outline_edit"

    # report event
    report_chunk = "report_chunk"
    final_report = "final_report"

    # thinking event
    thinking_tool_calls = "thinking_tool_calls"
    thinking_tool_calls_result = "thinking_tool_calls_result"
    thinking_message_chunk = "thinking_message_chunk"
    thinking_report_outline_generating = "thinking_report_outline_generating"
    thinking_step_outline = "thinking_step_outline"
    thinking_step_topic = "thinking_step_topic"
    thinking_step_report_generating = "thinking_step_report_generating"


class MessageToolCallContent(BaseModel):
    index: Optional[int] = Field(None, description="Tool call index")
    id: Optional[str] = Field(None, description="Tool call id")
    name: Optional[str] = Field(None, description="Tool call name")
    args: Optional[Any] = Field(None, description="Tool call args")
    result: Optional[Any] = Field(None, description="Tool call result")


class MessageContent(BaseModel):
    text: Optional[str] = Field(None, description="Text content")
    tool_calls: Optional[List[MessageToolCallContent]] = Field(
        None, description="Tool call content"
    )


class Message(BaseModel):
    id: Optional[str] = Field(None, description="Message id")
    parent_message_id: Optional[str] = Field(None, description="Parent message id")
    content: MessageContent = Field(..., description="Message content")
    content_type: MessageContentType = Field(..., description="Content type")


class Metadata(BaseModel):
    input_tokens: int = Field(..., description="Number of input tokens")
    output_tokens: int = Field(..., description="Number of output tokens")
    time: float = Field(..., description="Processing time in seconds")


class StreamEvent(BaseModel):
    event: EventType = Field(
        ..., description="Type of event, e.g., message_chunk, tool_calls, final_report"
    )
    run_id: str = Field(..., description="Unique run identifier")
    conversation_id: str = Field(..., description="Unique conversation identifier")
    error_code: int = Field(0, description="Error code, if any")
    error_msg: str = Field("", description="Error message, if any")
    messages: List[Message] = Field(..., description="Messages of the event")
    metadata: Optional[Metadata] = Field(
        None, description="Metadata including token counts and processing time"
    )