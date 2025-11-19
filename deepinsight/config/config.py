# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import os
from typing import Optional, List

import yaml
from pydantic import BaseModel, Field

from deepinsight.config.app_config import AppInfo
from deepinsight.config.database_config import DatabaseConfig
from deepinsight.config.prompt_management_config import PromptManagementConfig
from deepinsight.config.llm_config import LLMConfig
from deepinsight.config.scenarios_config import ScenariosConfig
from deepinsight.config.rag_config import RAGConfig
from deepinsight.config.workspace_config import WorkspaceConfig


class Config(BaseModel):
    database: DatabaseConfig = Field(..., description="Database configuration")
    app: AppInfo = Field(..., description="Application general configuration")

    # 可缺省的配置，若 YAML 中无此节，使用默认值以兼容现有简版 config.yaml
    prompt_management: PromptManagementConfig = Field(
        default_factory=PromptManagementConfig,
        description="Prompt management configuration",
    )
    llms: List[LLMConfig] = Field(
        default_factory=list,
        description="Default llm config",
    )
    scenarios: Optional[ScenariosConfig] = Field(
        default_factory=ScenariosConfig,
        description="Scenarios config",
    )
    rag: RAGConfig = Field(
        default_factory=RAGConfig,
        description="RAG working path configuration",
    )

    workspace: WorkspaceConfig = Field(
        default_factory=WorkspaceConfig,
        description="General workspace path configuration",
    )


CONFIG: Optional[Config] = None


def load_config(path: str) -> Config:
    global CONFIG
    with open(path, "r") as f:
        raw = f.read()
    expanded = os.path.expandvars(raw)
    data = yaml.safe_load(expanded) or {}
    CONFIG = Config(**data)
    return CONFIG