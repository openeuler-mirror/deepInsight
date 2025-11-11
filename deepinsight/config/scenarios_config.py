# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from typing import Optional, Dict
from pydantic import BaseModel, Field
from typing import Optional, Dict


class StreamBlocklistConfig(BaseModel):
    text: Optional[Dict[str, bool]] = Field(None)
    tool_call: Optional[Dict[str, bool]] = Field(None)


class DeepResearch(BaseModel):
    final_report_model: Optional[str] = Field(None, description="Final report model")
    allow_user_clarification: Optional[bool] = Field(False)
    allow_edit_research_brief: Optional[bool] = Field(False)
    allow_edit_report_outline: Optional[bool] = Field(False)
    stream_blocklist: Optional[StreamBlocklistConfig] = Field(None)


class ScenariosConfig(BaseModel):
    deep_research: Optional[DeepResearch] = Field(None)