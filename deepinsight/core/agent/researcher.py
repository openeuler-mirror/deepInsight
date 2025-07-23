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
Desc: 执行调研任务Agent
"""

from .base_agent import BaseAgent
from .planner import PlannerAgent
import asyncio
from typing import Any, Dict, List

class Researcher(BaseAgent):
    """
    Researcher 继承自 BaseAgent，根据调研计划并行执行 research。
    """
    def __init__(self, llm_model: str, mcp_command: tuple[str, ...]):
        super().__init__(llm_model, mcp_command)

    async def research(self, plan: PlannerAgent.Plan, context: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        """
        并行执行调研计划中的每个ResearchStep。
        返回每个step的结果列表。
        """
        async def run_step(step):
            result = await self.run(step.description, context)
            return {
                'step_number': step.step_number,
                'description': step.description,
                'expected_output': step.expected_output,
                'tools': step.tools,
                'result': result
            }
        tasks = [run_step(step) for step in plan.steps]
        return await asyncio.gather(*tasks)

    def build_system_prompt(self) -> str:
        """
        设定Researcher的系统提示词。
        """
        return (
            "你是专业的调研执行专家，擅长根据调研计划的每一步，精准、简明地完成调研任务。"
            "请用中文输出，确保内容准确、结构清晰。"
        )

    def build_user_prompt(self, *, query: str, context: Dict[str, Any] | None = None) -> str:
        """
        拼装每一步调研的用户输入。
        """
        ctx = "\n".join(f"{k}: {v}" for k, v in (context or {}).items())
        return f"{ctx}\n\n请根据以下调研任务，输出简明、准确的调研结果：\n{query}"

    async def parse_output(self, raw: str) -> str:
        """
        对LLM输出做结构化或后处理。
        """
        return raw.strip()
