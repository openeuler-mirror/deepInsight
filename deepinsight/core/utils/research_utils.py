# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from __future__ import annotations

import operator
from typing import Any, Dict, List
import logging

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
import yaml

from deepinsight.core.types.graph_config import ResearchConfig, ExpertDef


def parse_research_config(config: Dict[str, Any] | RunnableConfig) -> ResearchConfig:
    """
    将 LangGraph 的 `config`/`graph_config` 字典解析为 ResearchRuntimeOptions。

    支持两种输入：
    - 完整的 `graph_config`，其中包含 `configurable` 字段
    - 节点级的 `config`，其本身就是 `configurable` 字段
    """
    if config is None:
        raise ValueError("config must not be None")

    raw_cfg = config.get("configurable") if "configurable" in config else config
    if raw_cfg is None:
        raise ValueError("config must contain 'configurable' section or be that section itself")

    return ResearchConfig(**raw_cfg)

def override_reducer(current_value, new_value):
    """Reducer function that allows overriding values in state."""
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)
    else:
        return operator.add(current_value, new_value)

def dict_merge_reducer(
        current: Dict[str, str], update: Dict[str, str]
) -> Dict[str, str]:
    if current is None:
        current = {}
    newd = dict(current)
    for k, v in update.items():
        newd[k] = v
    return newd


def load_expert_config(expert_config_path: str) -> List[ExpertDef]:
    try:
        with open(expert_config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logging.warning(f"Expert config file not found: {expert_config_path}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error reading expert config: {e}")
        raise

    if not data:
        logging.info("Expert config file is empty.")
        return []

    if not isinstance(data, list):
        logging.warning("Expert config should be a list — ignoring invalid format.")
        return []

    try:
        return [ExpertDef(**item) for item in data]
    except Exception as e:
        logging.error(f"Failed to initialize Expert objects: {e}")
        return []
