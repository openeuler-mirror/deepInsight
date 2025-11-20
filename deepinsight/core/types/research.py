# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import uuid
from enum import Enum
from typing import Optional, Dict, Any, List, Union

from pydantic import BaseModel, Field

from langchain_core.tools import tool

class WebSearchResult(BaseModel):
    """Web 搜索工具返回的单条结果。"""
    title: Optional[str] = Field(None, description="Title of the web search result")
    url: Optional[str] = Field(None, description="URL of the result")
    icon: Optional[str] = Field(None, description="Favicon URL")


class ErrorResult(BaseModel):
    """工具调用失败时的统一错误结构。"""
    error: str = Field(..., description="Error message")


class ToolType(str, Enum):
    """工具类型枚举。"""
    web_search = "web_search"
    knowledge_base = "knowledge_base"


class ToolUnifiedResponse(BaseModel):
    """工具调用的统一响应结构。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_message_id: Optional[str] = None
    type: ToolType
    name: str
    args: Dict[str, Any]
    result: Union[List[WebSearchResult], ErrorResult]


class FinalResult(BaseModel):
    """深度研究最终结果。"""
    final_report: str = Field(..., description="Final report")
    expert_review_comments: Optional[Dict[str, str]] = Field(
        None, description="Expert review comments"
    )


class ClarifyNeedUser(BaseModel):
    """向用户澄清问题的请求。"""
    question: str = Field(
        ..., description="A question to ask the user to clarify the report scope"
    )


class WaitResearchBriefEdit(BaseModel):
    """请求用户编辑研究简要。"""
    research_brief: str = Field(
        ..., description="A research brief to ask the user to edit"
    )


class WaitReportOutlineEdit(BaseModel):
    """请求用户编辑报告大纲。"""
    report_outline: str = Field(
        ..., description="A report outline to ask the user to edit"
    )

class ResearchComplete(BaseModel):
    """Call this tool to indicate that the research is complete."""

@tool(description="Strategic reflection tool for research planning")
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    When to use:
    - After receiving search results: What key information did I find?
    - Before deciding next steps: Do I have enough to answer comprehensively?
    - When assessing research gaps: What specific information am I still missing?
    - Before concluding research: Can I provide a complete answer now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    return f'''反思已记录：{reflection}。在反思过程中，请严格依据上下文内容和工具使用限制进行总结。务必明确以下要求：
1. 工具的适用范围：只能用于支持的操作与字段，不得超出。
2. 输出内容应结合实际场景，说明哪些可以完成，哪些不可以完成，避免模糊表述。
3. 反思要体现具体问题与解决思路，不得泛泛而谈。'''


class Summary(BaseModel):
    """Web 搜索结果的摘要。"""
    summary: str = Field(..., description="Summary of the web search result")
    key_excerpts: str = Field(..., description="Key excerpts from the result")