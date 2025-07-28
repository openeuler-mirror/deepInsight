# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from sqlalchemy.orm import Session
from typing import List, Optional

from deepinsight.stores.postgresql.repositories.base_repository import BaseRepository
from deepinsight.stores.postgresql.schemas.message import Message


class MessageRepository(BaseRepository[Message]):
    """消息数据访问类"""
    
    def __init__(self, db: Session):
        """初始化消息仓库"""
        super().__init__(db, Message)
    
    def get_by_id(self, message_id: str) -> Optional[Message]:
        """
        根据消息ID获取消息
        
        :param message_id: 消息ID
        :return: 找到的消息或None
        """
        return self.db.query(Message).filter(Message.message_id == message_id).first()
    
    def get_by_conversation_id(self, conversation_id: str, skip: int = 0, limit: int = 100) -> List[Message]:
        """
        根据对话ID获取消息列表
        
        :param conversation_id: 对话ID
        :param skip: 跳过的记录数
        :param limit: 最大返回记录数
        :return: 消息列表
        """
        return self.db.query(Message)\
            .filter(Message.conversation_id == conversation_id)\
            .order_by(Message.created_time.asc())\
            .offset(skip)\
            .limit(limit)\
            .all()

    def get_all_by_conversation_id(self, conversation_id: str) -> List[Message]:
        """
        根据对话ID获取消息列表

        :param conversation_id: 对话ID
        :param skip: 跳过的记录数
        :param limit: 最大返回记录数
        :return: 消息列表
        """
        return self.db.query(Message) \
            .filter(Message.conversation_id == conversation_id) \
            .order_by(Message.created_time.asc()) \
            .all()
    
    def get_by_type(self, conversation_id: str, message_type: str) -> List[Message]:
        """
        根据类型获取特定对话中的消息
        
        :param conversation_id: 对话ID
        :param message_type: 消息类型
        :return: 消息列表
        """
        return self.db.query(Message)\
            .filter(
                Message.conversation_id == conversation_id,
                Message.type == message_type
            )\
            .order_by(Message.created_time.asc())\
            .all()
    
    def delete_by_conversation_id(self, conversation_id: str) -> None:
        """
        删除特定对话的所有消息
        
        :param conversation_id: 对话ID
        """
        self.db.query(Message)\
            .filter(Message.conversation_id == conversation_id)\
            .delete()
        self.db.commit()
