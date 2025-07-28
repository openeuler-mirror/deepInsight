# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import asyncio
import os
import uuid
from datetime import datetime
from typing import Optional, Dict

from fastapi import FastAPI, Request, APIRouter, HTTPException, Query, Body, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from deepinsight.service.conversation import ConversationService
from deepinsight.service.deep_research import MessageType, DeepResearchService
from deepinsight.service.schemas.conversation import (ConversationListRsp, ConversationListMsg, ConversationListItem,
                                                      AddConversationRsp, BodyAddConversation, AddConversationMsg,
                                                      ResponseData, DeleteConversationData, RenameConversationData,
                                                      BodyGetList)
from deepinsight.service.schemas.chat import GetChatHistoryData, GetChatHistoryStructure, GetChatHistoryRsp

# 读取环境变量中的 API 前缀
API_PREFIX = os.getenv("API_PREFIX", "")

# 创建 FastAPI 实例
app_instance = FastAPI(
    title="DeepInsight API",
    description="A streaming chat API for DeepInsight",
    version="1.0.0"
)
_conversations: Dict[str, ConversationListItem] = {}
# 创建路由
router = APIRouter()

# 跨域中间件配置
app_instance.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@router.get("/api/conversations", response_model=ConversationListRsp, tags=["conversation"])
async def get_conversation_list(body: BodyGetList = Depends()):
    try:
        conversation_list = ConversationService.get_list(user_id=body.user_id, offset=body.offset, limit=body.limit)
        return ConversationListRsp(
            code=0,
            message="OK",
            data=ConversationListMsg(conversations=conversation_list)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# temporarily deprecated
@router.post("/api/conversation", response_model=AddConversationRsp, tags=["conversation"])
async def add_conversation(
        body: BodyAddConversation = Body(...)
):
    try:
        new_conversation = ConversationService.add_conversation(user_id=body.user_id, title=body.title,
                                                                conversation_id=body.conversation_id)

        return AddConversationRsp(
            code=0,
            message="OK",
            data=AddConversationMsg(conversationId=str(new_conversation.conversation_id),
                                    created_time=str(new_conversation.created_time))
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/conversation", response_model=ResponseData, tags=["conversation"])
async def delete_conversation(data: DeleteConversationData = Body(...)):
    try:
        for cid in data.conversation_list:
            ConversationService.del_conversation(conversation_id=cid)
        return ResponseData(code=0, message="Deleted", data={})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/conversation", response_model=ResponseData, tags=["conversation"])
async def rename_conversation(data: RenameConversationData = Body(...)):
    try:
        conversation, is_succeed = ConversationService.rename_conversation(conversation_id=data.conversation_id,
                                                                           new_name=data.new_name)
        if is_succeed:
            return ResponseData(code=0, message="Modified", data={"new_name": data.new_name})
        else:
            return ResponseData(code=100, message="Conversation Not Found", data={"new_name": data.new_name})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/conversations/{conversation_id}/messages", response_model=GetChatHistoryRsp, tags=["conversation"])
async def get_conversation_messages(conversation_id: str):
    conversation_info = ConversationService.get_conversation_info(conversation_id)
    history_present = ConversationService.get_history_messages(conversation_id)
    new_data = GetChatHistoryStructure(
        conversation_id=conversation_id,
        user_id=conversation_info.user_id,
        created_time=str(conversation_info.created_time),
        title=conversation_info.title,
        status=conversation_info.status,
        messages=history_present
    )
    return GetChatHistoryRsp(code=0, message="ok", data=new_data)


app_instance.include_router(router, prefix=API_PREFIX)
