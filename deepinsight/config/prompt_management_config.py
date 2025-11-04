# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from typing import Optional
from pydantic import BaseModel, Field


class PromptManagementConfig(BaseModel):
    """提示词管理配置（占位，提供默认值以兼容缺失字段）"""

    enabled: bool = Field(default=False, description="Enable prompt management features")
    storage_path: Optional[str] = Field(default=None, description="Prompt storage path")
    max_history: int = Field(default=50, description="Max stored prompt history count")