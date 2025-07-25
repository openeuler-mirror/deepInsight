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
from typing import Optional, Union, List, Dict, Any, Literal, Generic, TypeVar
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime

T = TypeVar('T')

class MessageType(str, Enum):
    """
    Enumeration of core message types for streaming protocol.

    Defines the different categories of messages that can be exchanged in the streaming system.
    """
    START = "start"         # Stream initialization
    CHUNK = "chunk"         # Data payload chunk
    END = "end"             # Normal termination
    ERROR = "error"         # Error termination
    COMPLETE = "complete"   # Regular non-stream response
    HEARTBEAT = "heartbeat" # Keep-alive ping
    CONTROL = "control"     # Flow control


class BaseMessage(BaseModel):
    """
    Base message structure containing common fields for all message types.

    Attributes:
        model_config: Pydantic configuration to serialize enums by value
        stream_id: Unique identifier for the stream session
        message_type: Type of the message (from MessageType enum)
        timestamp: Message creation timestamp (auto-generated)
        metadata: Additional metadata dictionary
    """
    model_config = ConfigDict(use_enum_values=True)

    # Identifiers
    stream_id: str = Field(..., description="Unique stream session ID")

    # Protocol control
    message_type: MessageType
    timestamp: datetime = Field(default_factory=datetime.now)

    # metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StartMessage(BaseMessage, Generic[T]):
    """
    Message indicating the start of a new stream.

    Attributes:
        message_type: Fixed as MessageType.START
        payload: Generic payload data marking the stream start
    """
    message_type: Literal[MessageType.START] = MessageType.START
    payload: T


class ChunkMessage(BaseMessage, Generic[T]):
    """
    Message containing a data chunk in a stream.

    Attributes:
        message_type: Fixed as MessageType.CHUNK
        payload: Generic payload data for this chunk
    """
    message_type: Literal[MessageType.CHUNK] = MessageType.CHUNK
    payload: T


class EndMessage(BaseMessage, Generic[T]):
    """
    Message indicating successful stream completion.

    Attributes:
        message_type: Fixed as MessageType.END
        payload: Generic payload data marking the stream end
    """
    message_type: Literal[MessageType.END] = MessageType.END
    payload: T


class ErrorMessage(BaseMessage):
    """
    Message indicating error termination of a stream.

    Attributes:
        message_type: Fixed as MessageType.ERROR
        error_code: Numeric error code
        error_message: Human-readable error description
    """
    message_type: Literal[MessageType.ERROR] = MessageType.ERROR
    error_code: int
    error_message: str


class CompleteMessage(BaseMessage, Generic[T]):
    """
     Message containing a complete non-stream response.

     Attributes:
         message_type: Fixed as MessageType.COMPLETE
         payload: Generic payload data for complete response
     """
    message_type: Literal[MessageType.COMPLETE] = MessageType.COMPLETE
    payload: T


class HeartbeatMessage(BaseMessage):
    """
    Keep-alive message for connection monitoring.

    Attributes:
        message_type: Fixed as MessageType.HEARTBEAT
        latency_ms: Optional latency measurement in milliseconds
    """
    message_type: Literal[MessageType.HEARTBEAT] = MessageType.HEARTBEAT
    latency_ms: Optional[int] = Field(None, ge=0)

# Union type representing all possible message types
Message = Union[
    StartMessage[T],
    ChunkMessage[T],
    EndMessage[T],
    ErrorMessage,
    CompleteMessage[T],
    HeartbeatMessage,
]