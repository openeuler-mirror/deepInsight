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
from deepinsight.stores.postgresql.schemas.report import Report


class ReportRepository(BaseRepository[Report]):
    """报告数据访问类"""
    
    def __init__(self, db: Session):
        """初始化报告仓库"""
        super().__init__(db, Report)
    
    def get_by_id(self, report_id: str) -> Optional[Report]:
        """
        根据报告ID获取报告
        
        :param report_id: 报告ID
        :return: 找到的报告或None
        """
        return self.db.query(Report).filter(Report.report_id == report_id).first()
    
    def get_by_message_id(self, message_id: str) -> Optional[Report]:
        """
        根据消息ID获取报告
        
        :param message_id: 消息ID
        :return: 找到的报告或None
        """
        return self.db.query(Report).filter(Report.message_id == message_id).first()
    
    def get_by_conversation_id(self, conversation_id: str, skip: int = 0, limit: int = 100) -> List[Report]:
        """
        根据对话ID获取报告列表
        
        :param conversation_id: 对话ID
        :param skip: 跳过的记录数
        :param limit: 最大返回记录数
        :return: 报告列表
        """
        return self.db.query(Report)\
            .filter(Report.conversation_id == conversation_id)\
            .order_by(Report.created_time.desc())\
            .offset(skip)\
            .limit(limit)\
            .all()
    
    def delete_by_conversation_id(self, conversation_id: str) -> None:
        """
        删除特定对话的所有报告
        
        :param conversation_id: 对话ID
        """
        self.db.query(Report)\
            .filter(Report.conversation_id == conversation_id)\
            .delete()
        self.db.commit()
