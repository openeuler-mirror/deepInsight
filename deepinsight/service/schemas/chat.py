# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from datetime import datetime
from typing import Union, List

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """
    Represents a single message within a conversation.
    """
    id: str
    content: Union[str, List]
    role: str
    created_at: datetime


class GetChatHistoryData(BaseModel):
    """
    Schema for the request body when fetching chat history.
    """
    conversationId: str = Field(alias="conversationId")


class GetChatHistoryStructure(BaseModel):
    """
    Represents the structured data for a chat history response.
    """
    conversation_id: str = Field(alias="conversationId")
    user_id: str = Field(alias="userId")
    created_time: str
    title: str
    status: str
    messages: List[ChatMessage]


class GetChatHistoryRsp(BaseModel):
    """
    The complete response schema for fetching chat history.
    """
    code: int
    message: str
    data: GetChatHistoryStructure
