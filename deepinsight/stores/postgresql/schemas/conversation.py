# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from deepinsight.stores.postgresql.database import DatabaseModel


class Conversation(DatabaseModel):
    """对话表模型"""
    __tablename__ = "conversation"
    
    conversation_id = Column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4,
        comment="对话ID"
    )
    user_id = Column(
        String(36), 
        nullable=False,
        comment="用户ID"
    )
    created_time = Column(
        DateTime(timezone=True), 
        nullable=False, 
        default=func.now(),
        comment="创建时间"
    )
    title = Column(
        String(255), 
        default="新建对话",
        comment="对话标题"
    )
    status = Column(
        String(50), 
        nullable=False, 
        default="active",
        comment="对话状态"
    )
    
    def __repr__(self):
        return f"<Conversation(id={self.conversation_id}, user_id={self.user_id}, title={self.title})>"
