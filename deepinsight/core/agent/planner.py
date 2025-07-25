"""
Copyright (c) 2025-2025 Huawei Technologies Co., Ltd.

deepInsight is licensed under Mulan PSL v2.
You can use this software according to the terms and conditions of the Mulan PSL v2.
You may obtain a copy of Mulan PSL v2 at:
    http://license.coscl.org.cn/MulanPSL2
THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY OR FIT FOR A PARTICULAR
PURPOSE.
See the Mulan PSL v2 for more details.
Created: 2025-07-25
Desc: 生成研究计划Agent
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from .base_agent import BaseAgent


class PlannerAgent(BaseAgent):
    """调研计划生成器：输入问题 → 输出 Plan"""

    # ---------- 输出结构 ----------
    class ResearchStep(BaseModel):
        step_number: int
        description: str
        expected_output: str
        tools: List[str] = Field(default_factory=list)

    class Plan(BaseModel):
        goal: str
        steps: List[ResearchStep]

    # ---------- 1. system prompt ----------
    def build_system_prompt(self) -> str:
        return (
            "你是资深研究策划师。你的唯一职责是把用户问题拆解成"
            "可执行的调研步骤，并用合法 JSON 输出计划。"
        )

    # ---------- 2. user prompt ----------
    def build_user_prompt(
        self,
        *,
        query: str,
        context: Dict[str, Any] | None = None,
    ) -> str:
        ctx = "\n".join(f"{k}: {v}" for k, v in (context or {}).items())
        return f"""{ctx}

请根据以下问题生成调研计划，JSON 格式：
{{
  "goal": "<调研目标>",
  "steps": [
    {{
      "step_number": 1,
      "description": "<步骤描述>",
      "expected_output": "<预期输出>",
      "tools": ["<可能用到的工具名>", "..."]
    }}
  ]
}}

问题：{query}"""

    # ---------- 3. MCP 连接 ----------
    # 复用父类 connect_mcp，无需改动

    # ---------- 4. 输出解析 ----------
    async def parse_output(self, raw: str) -> Plan:
        """把 LLM 返回的 JSON 解析成 Plan 对象"""
        try:
            return self.Plan(**json.loads(raw))
        except Exception as e:
            # 简易容错：抛异常或再次让 LLM 修正
            raise ValueError(f"JSON 解析失败：{e}") from e

    # ---------- 业务快捷入口 ----------
    async def generate_plan(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
    ) -> Plan:
        """外部直接调用"""
        plan_json = await self.run(query=query, context=context)
        return await self.parse_output(plan_json)
