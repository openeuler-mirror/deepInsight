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
from deepinsight.stores.postgresql.schemas.conversation import Conversation


class ConversationRepository(BaseRepository[Conversation]):
    """对话数据访问类"""
    
    def __init__(self, db: Session):
        """初始化对话仓库"""
        super().__init__(db, Conversation)
    
    def get_by_id(self, conversation_id: str) -> Optional[Conversation]:
        """
        根据对话ID获取对话
        
        :param conversation_id: 对话ID
        :return: 找到的对话或None
        """
        return self.db.query(Conversation).filter(Conversation.conversation_id == conversation_id).first()
    
    def get_by_user_id(self, user_id: str, offset: int = 0, limit: int = 100) -> List[Conversation]:
        """
        根据用户ID获取对话列表
        
        :param user_id: 用户ID
        :param offset: 跳过的记录数
        :param limit: 最大返回记录数
        :return: 对话列表
        """
        return self.db.query(Conversation)\
            .filter(Conversation.user_id == user_id)\
            .order_by(Conversation.created_time.desc())\
            .offset(offset)\
            .limit(limit)\
            .all()
    
    def get_active_by_user_id(self, user_id: str) -> List[Conversation]:
        """
        获取用户的活跃对话
        
        :param user_id: 用户ID
        :return: 活跃对话列表
        """
        return self.db.query(Conversation)\
            .filter(
                Conversation.user_id == user_id,
                Conversation.status == "active"
            )\
            .order_by(Conversation.created_time.desc())\
            .all()
    
    def update_status(self, conversation_id: str, status: str) -> Optional[Conversation]:
        """
        更新对话状态
        
        :param conversation_id: 对话ID
        :param status: 新状态
        :return: 更新后的对话或None
        """
        conversation = self.get_by_id(conversation_id)
        if conversation:
            conversation.status = status
            self.db.commit()
            self.db.refresh(conversation)
        return conversation
