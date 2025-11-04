# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from typing import Any, Optional

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 配置模型
    与前端和配置文件结构保持一致：
    - type: 供应商类型（openai、deepseek、anthropic、tongyi 等）
    - model: 模型名称（如 gpt-4、deepseek-chat 等）
    - base_url: 供应商或代理的基础 URL（可选）
    - api_key: API Key（可选）
    - setting: 生成参数（LLMSetting，可选）
    """

    type: str = Field(..., description="LLM provider, e.g., openai, deepseek, anthropic")
    model: str = Field(..., description="Model name, e.g., gpt-4")
    base_url: Optional[str] = Field(None, description="Model API base URL")
    api_key: Optional[str] = Field(None, description="Model API key")
    setting: Optional[Any] = Field(
        None, description="Configuration for model generation parameters"
    )
