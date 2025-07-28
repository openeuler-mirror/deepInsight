# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from sqlalchemy import Column, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from deepinsight.stores.postgresql.database import DatabaseModel


class Report(DatabaseModel):
    """报告表模型"""
    __tablename__ = "report"
    
    report_id = Column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4,
        comment="报告ID"
    )
    message_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("message.message_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="关联的消息ID"
    )
    conversation_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("conversation.conversation_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的对话ID"
    )
    thought = Column(
        Text,
        comment="思考过程"
    )
    report_content = Column(
        Text, 
        nullable=False,
        comment="报告内容"
    )
    created_time = Column(
        DateTime(timezone=True), 
        nullable=False, 
        default=func.now(),
        comment="创建时间"
    )
    
    # 关系
    conversation = relationship("Conversation", backref="reports")
    
    def __repr__(self):
        return f"<Report(id={self.report_id}, message_id={self.message_id})>"
