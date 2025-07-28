# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field
from datetime import datetime
from pymongo import AsyncMongoClient
from typing import TYPE_CHECKING, Optional
import uuid


class Session(BaseModel):
    """
    Session

    collection: session
    """

    id: str = Field(alias="_id")
    ip: str
    user_sub: Optional[str] = Field(default=None)
    nonce: Optional[str] = Field(default=None)
    expired_at: datetime


class Task(BaseModel):
    """
    collection: witchiand_task
    """

    task_id: uuid.UUID = Field(alias="_id")
    status: str
    created_time: datetime = Field(default_factory=datetime.now)


if TYPE_CHECKING:
    from pymongo.asynchronous.client_session import AsyncClientSession
    from pymongo.asynchronous.collection import AsyncCollection


class MongoDB:
    """MongoDB连接"""

    user = os.getenv('MONGODB_USER', 'admin')
    password = os.getenv('MONGODB_PASSWORD', '')
    host = os.getenv('MONGODB_HOST', 'localhost')
    port = os.getenv('MONGODB_PORT', 27017)
    _client: AsyncMongoClient = AsyncMongoClient(
        f"mongodb://{user}:{password}@{host}:{port}/?directConnection=true&replicaSet=rs0",
        uuidRepresentation="standard"
    )

    @classmethod
    def get_collection(cls, collection_name: str) -> AsyncCollection:
        """获取MongoDB集合（表）"""
        try:
            return cls._client[os.getenv('MONGODB_DATABASE', '')][collection_name]
        except Exception as e:
            logging.exception("[MongoDB] 获取集合 %s 失败", collection_name)
            raise RuntimeError(str(e)) from e

    @classmethod
    async def clear_collection(cls, collection_name: str) -> None:
        """清空MongoDB集合（表）"""
        try:
            await cls._client[os.getenv('MONGODB_DATABASE', '')][collection_name].delete_many({})
        except Exception:
            logging.exception("[MongoDB] 清空集合 %s 失败", collection_name)

    @classmethod
    def get_session(cls) -> AsyncClientSession:
        """获取MongoDB会话"""
        return cls._client.start_session()
