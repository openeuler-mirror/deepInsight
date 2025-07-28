# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from enum import Enum


class AgentType(str, Enum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    REPORTER = "reporter"


class AgentMessageAdditionType(str, Enum):
    TIPS = "tips"


class AgentExecutePhase(str, Enum):
    REPORT_PLANING = "report_planning"
    REPORT_WRITING = "report_writing"
