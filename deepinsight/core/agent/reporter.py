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
Desc: 生成报告Agent
"""

from .base_agent import BaseAgent
from typing import Any, Dict, List

class Reporter(BaseAgent):
    """
    Reporter 继承自 BaseAgent，根据 research 结果生成最终报告。
    """
    def __init__(self, llm_model: str, mcp_command: tuple[str, ...]):
        super().__init__(llm_model, mcp_command)

    async def generate_report(self, research_results: List[Dict[str, Any]], context: Dict[str, Any] | None = None) -> str:
        """
        根据 research 结果生成最终报告。
        """
        # 将每个step的结果拼接成报告内容
        steps_summary = "\n".join(
            f"步骤{r['step_number']}：{r['description']}\n结果：{r['result']}" for r in research_results
        )
        query = f"请根据以下调研步骤及其结果，生成结构化、条理清晰的最终调研报告：\n{steps_summary}"
        return await self.run(query, context)

    def build_system_prompt(self) -> str:
        """
        设定Reporter的系统提示词。
        """
        return (
            "你是专业的调研执行专家，擅长根据调研计划的每一步，精准、简明地完成调研任务。"
            "请用中文输出，确保内容准确、结构清晰。"
        )

    def build_user_prompt(self, *, query: str, context: Dict[str, Any] | None = None) -> str:
        """
        设定Reporter的用户提示词。
        """
        ctx = "\n".join(f"{k}: {v}" for k, v in (context or {}).items())
        return f"{ctx}\n\n请根据以下调研任务，输出简明、准确的调研结果：\n{query}"

    async def parse_output(self, raw: str) -> str:
        """
        对LLM输出做结构化或后处理。
        """
        return raw.strip()