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
Desc: Agent基类以及公共方法
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio


class BaseAgent:
    """
    极简 BaseAgent：只做四件事
    1. build_system_prompt  → 系统级提示词
    2. build_user_prompt    → 用户级提示词
    3. connect_mcp          → 连接 MCP Server
    4. parse_output         → 解析 LLM 原始输出
    """

    def __init__(
        self,
        llm_model: str,
        mcp_command: tuple[str, ...],
    ) -> None:
        self.llm_model = llm_model
        self.mcp_command = mcp_command

        # 运行时句柄
        self.mcp_server: MCPServerStdio | None = None
        self.agent: Agent[None, str] | None = None

    # ---------------- 1. 系统提示词 ---------------- #
    def build_system_prompt(self) -> str:
        """子类可覆写，用于设定角色、规则、输出格式等"""
        return (
            "你是由 Camel-AI 驱动的智能助手，擅长使用工具完成任务。"
            "请始终用中文回答，并确保输出格式简洁、准确。"
        )

    # ---------------- 2. 用户提示词 ---------------- #
    def build_user_prompt(
        self,
        *,
        query: str,
        context: Dict[str, Any] | None = None,
    ) -> str:
        """子类可覆写，用于拼装用户输入"""
        ctx = "\n".join(f"{k}: {v}" for k, v in (context or {}).items())
        return f"{ctx}\n\n用户问题：{query}"

    # ---------------- 3. MCP 连接 ---------------- #
    async def connect_mcp(self) -> None:
        """启动 MCP Server 并初始化 Agent"""
        self.mcp_server = MCPServerStdio(*self.mcp_command)
        self.agent = Agent(
            model=self.llm_model,
            deps_type=None,
            result_type=str,
            system_prompt=self.build_system_prompt(),
            toolsets=[self.mcp_server],
        )

    # ---------------- 4. 输出解析 ---------------- #
    async def parse_output(self, raw: str) -> str:
        """子类可覆写，用于结构化/后处理"""
        return raw.strip()

    # ---------------- 便捷运行入口 ---------------- #
    async def run(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
    ) -> str:
        if self.agent is None:
            await self.connect_mcp()

        prompt = self.build_user_prompt(query=query, context=context)
        async with self.mcp_server:  # type: ignore
            result = await self.agent.run(prompt)  # type: ignore
            return await self.parse_output(result.data)