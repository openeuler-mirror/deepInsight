# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from typing import List

from deepinsight.service.deep_research import DeepResearchService
from deepinsight.service.schemas.chat import ChatMessage
from deepinsight.service.schemas.conversation import ConversationListItem
from deepinsight.stores.postgresql.database import get_database_session
from deepinsight.stores.postgresql.repositories.conversation_repository import ConversationRepository
from deepinsight.stores.postgresql.repositories.message_repository import MessageRepository
from deepinsight.stores.postgresql.repositories.report_repository import ReportRepository


class ConversationService:
    @classmethod
    def get_list(cls, user_id, offset: int = 0, limit: int = 100):
        db = get_database_session()
        conversation_repo = ConversationRepository(db)
        conversation_list = conversation_repo.get_by_user_id(user_id=user_id, offset=offset, limit=limit)
        conv_item_list = []
        for conv in conversation_list:
            conv_item_list.append(ConversationListItem(
                conversationId=str(conv.conversation_id),
                title=conv.title,
                createdTime=str(conv.created_time)
            )
            )
        return conv_item_list

    @classmethod
    def del_conversation(cls, conversation_id):
        db = get_database_session()
        conversation_repo = ConversationRepository(db)
        message_repo = MessageRepository(db)
        report_repo = ReportRepository(db)
        message_repo.delete_by_conversation_id(conversation_id=conversation_id)
        report_repo.delete_by_conversation_id(conversation_id=conversation_id)
        conversation_repo.delete_conversation(conversation_id=conversation_id)

    @classmethod
    def rename_conversation(cls, conversation_id, new_name):
        db = get_database_session()
        conversation_repo = ConversationRepository(db)
        return conversation_repo.update_title(conversation_id=conversation_id, new_title=new_name)

    @classmethod
    def add_conversation(cls, user_id, title, conversation_id):
        db = get_database_session()
        conversation_repo = ConversationRepository(db)
        saved_conversation = conversation_repo.create_conversation(user_id, title, conversation_id)
        return saved_conversation

    @classmethod
    def get_conversation_info(cls, conversation_id_str: str):
        db = get_database_session
        repository = ConversationRepository(db)
        return repository.get_by_id(conversation_id_str)

    @classmethod
    def get_history_messages(cls, conversation_id_str: str) -> List[ChatMessage]:
        db = get_database_session()
        repository = MessageRepository(db)
        messages_from_db = repository.get_by_conversation_id(conversation_id_str)

        processed_messages = []
        for msg in messages_from_db:
            content_to_use = msg.content
            if msg.type == "report":
                processed_report = DeepResearchService.get_report_and_thought_by_message_id(msg.message_id)
                content_to_use = processed_report.thought.messages + [processed_report.report]


            processed_message = ChatMessage(
                id=str(msg.message_id),
                content=content_to_use,
                role=msg.type,
                created_at=msg.created_time.isoformat() if msg.created_time else None
            )

            processed_messages.append(processed_message)
        return processed_messages


