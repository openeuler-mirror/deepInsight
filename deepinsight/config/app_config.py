# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from pydantic import BaseModel, Field


class AppInfo(BaseModel):
    """应用基础配置"""

    name: str = Field(default="deepinsight", description="App name")
    host: str = Field(default="0.0.0.0", description="Bind host")
    port: int = Field(default=8888, description="Bind port")
    api_prefix: str = Field(default="/api/v1", description="API prefix")
    reload: bool = Field(default=False, description="Enable auto reload in dev")