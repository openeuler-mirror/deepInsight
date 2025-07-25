# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class HistoricalMessageType(str, Enum):
    USER = "user"
    RESEARCH_PLAN = "research_plan"


class HistoricalMessage(BaseModel):
    """Represents a single historical message from the database"""
    content: str
    type: HistoricalMessageType
    created_time: datetime
    message_id: str