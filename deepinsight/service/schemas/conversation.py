# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from typing import Optional, List

from pydantic import BaseModel, Field


class ConversationListItem(BaseModel):
    conversationId: str
    title: str
    createdTime: str
    type: str = "normal"


class ConversationListMsg(BaseModel):
    conversations: List[ConversationListItem]


class ConversationListRsp(BaseModel):
    code: int
    message: str
    data: ConversationListMsg


class AddConversationMsg(BaseModel):
    conversationId: str
    created_time: str


class AddConversationRsp(BaseModel):
    code: int
    message: str
    data: AddConversationMsg


class BodyAddConversation(BaseModel):
    conversation_id: str
    user_id: str = "test_user"
    title: Optional[str] = ""


class ModifyConversationData(BaseModel):
    title: str = Field(..., min_length=1, max_length=2000)


class UpdateConversationRsp(BaseModel):
    code: int
    message: str
    data: ConversationListItem


class DeleteConversationData(BaseModel):
    conversation_list: List[str]


class ResponseData(BaseModel):
    code: int
    message: str
    data: Optional[dict] = {}


class RenameConversationData(BaseModel):
    conversation_id: str
    new_name: str
    ori_name: Optional[str] = "default name"


class BodyGetList(BaseModel):
    user_id: Optional[str] = "test_user"
    offset: Optional[int] = 0
    limit: Optional[int] = 100
