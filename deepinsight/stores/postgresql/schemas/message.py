# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from deepinsight.stores.postgresql.database import DatabaseModel


class Message(DatabaseModel):
    """消息表模型"""
    __tablename__ = "message"

    message_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="消息ID"
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversation.conversation_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的对话ID"
    )
    content = Column(
        Text,
        nullable=False,
        comment="消息内容"
    )
    type = Column(
        String(50),
        nullable=False,
        comment="消息类型：chat, planner, report"
    )
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        comment="创建时间"
    )

    # 关系
    conversation = relationship("Conversation", backref="messages")
    report = relationship("Report", backref="message", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Message(id={self.message_id}, conversation_id={self.conversation_id}, type={self.type})>"
