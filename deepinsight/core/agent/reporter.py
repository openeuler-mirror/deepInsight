# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import logging
import re

from camel.responses import ChatAgentResponse

from deepinsight.config.model import ModelConfig
from deepinsight.core.agent.base import BaseAgent, OutputType
from typing import Any, Dict, Generator
from typing import Optional, TypeAlias, Callable, List

from pydantic import BaseModel

from deepinsight.core.agent.researcher import ResearchExecution
from deepinsight.core.types.messages import Message
from deepinsight.core.prompt.prompt_template import GLOBAL_DEFAULT_PROMPT_REPOSITORY, PromptStage
from deepinsight.utils.parallel_worker_utils import Executor


class WritingTask(BaseModel):
    """
    Represents an individual writing task derived from research findings.

    Attributes:
        writing_task_id: Unique identifier for the writing task
        writing_name: Descriptive name for the task
        writing_instruction: Detailed instructions for content generation
        writing_content: Generated content (filled after execution)
        writing_need_info: Required research information for the task
    """
    writing_task_id: Optional[str] = None
    writing_name: Optional[str] = None
    writing_instruction: str
    writing_content: Optional[str] = None
    writing_need_info: Optional[str] = None


# Type alias for report plan parser functions
ReportPlanParser: TypeAlias = Callable[[str], List[WritingTask]]

# Type alias for repost post process functions
ReportPostProcesser: TypeAlias = Callable[[List[WritingTask]], str]


class GenerateSubTaskAgent(BaseAgent[List[WritingTask]]):
    """
    Specialized agent for generating structured sub tasks from research data.

    Inherits from BaseAgent with List[WritingTask] as the concrete output type.
    """
    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
            report_plan_parser: Optional[ReportPlanParser] = None,
    ) -> None:
        """
        Initialize the report planning agent.

        Args:
          model_config: Configuration for the AI model
          mcp_tools_config_path: Path to MCP tools configuration
          mcp_client_timeout: Timeout for MCP client operations
          report_plan_parser: Custom parser for converting LLM responses to writing tasks
        """
        super().__init__(model_config, mcp_tools_config_path, mcp_client_timeout)
        self.report_plan_parser = report_plan_parser

    def build_system_prompt(self) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(PromptStage.REPORT_PLAN_SYSTEM)

    def build_user_prompt(self, *, query: str, context: Dict[str, Any] | None = None) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(stage=PromptStage.REPORT_PLAN_USER, variables=dict(
            query=query,
            **context,
        ))

    def parse_output(self, response: ChatAgentResponse) -> OutputType:
        if self.report_plan_parser:
            return self.report_plan_parser(response.msg.content)
        return super().parse_output(response)


class ExecuteSubTaskAgent(BaseAgent[str]):
    """
     Specialized agent for executing individual writing tasks.

     Inherits from BaseAgent with str as the concrete output type.
     """

    def __init__(self, model_config: ModelConfig, mcp_tools_config_path: Optional[str] = None,
                 mcp_client_timeout: Optional[int] = None) -> None:
        """Initialize with model and MCP configuration."""
        super().__init__(model_config, mcp_tools_config_path, mcp_client_timeout)

    def build_system_prompt(self) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(PromptStage.REPORT_WRITE_SYSTEM)

    def build_user_prompt(self, *, query: str, context: Dict[str, Any] | None = None) -> str:
        return GLOBAL_DEFAULT_PROMPT_REPOSITORY.get_prompt(stage=PromptStage.REPORT_WRITE_USER, variables=dict(
            query=query,
            **context,
        ))

    def parse_output(self, response: ChatAgentResponse) -> OutputType:
        return response.msg.content


class Reporter:
    """
      Orchestrates the complete report generation workflow from research results.

      Coordinates:
      1. Planning writing tasks
      2. Executing writing tasks in parallel
      3. Assembling final report
      """

    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
            report_plan_parser: Optional[ReportPlanParser] = None,
            report_post_processer: Optional[ReportPostProcesser] = None,
    ):
        """
        Initialize the reporter with configuration and optional processors.

        Args:
            model_config: Configuration for the AI model
            mcp_tools_config_path: Path to MCP tools configuration
            mcp_client_timeout: Timeout for MCP client operations
            report_plan_parser: Custom parser for writing tasks
            report_post_processer: Custom post-processor for final report
        """
        self.model_config = model_config
        self.mcp_tools_config_path = mcp_tools_config_path
        self.mcp_client_timeout = mcp_client_timeout
        self.report_plan_parser = report_plan_parser or self._default_report_plan_parser
        self.report_post_processer = report_post_processer or self._default_report_post_processer

    def run(
            self,
            query: str,
            research_executions: List[ResearchExecution],
    ) -> Generator[Message, None, str]:
        """
        Execute the full report generation workflow.

        Args:
            query: The original research query
            research_executions: List of completed research executions

        Yields:
            Message: Progress messages during execution

        Returns:
            str: The final generated report
        """
        writing_tasks = yield from self._generate_writing_task(query=query, research_executions=research_executions)
        writing_report_executor = Executor("writing_report")

        def report_writing_worker(i, writing_task: WritingTask):
            one_writing_result = yield from self._write_task(
                query=query,
                writing_task=writing_task,
            )
            return one_writing_result or []

        all_content = yield from writing_report_executor(report_writing_worker,
                                                         list(enumerate(writing_tasks)))
        for i, content in enumerate(all_content):
            writing_tasks[i].writing_content = content

        return self.report_post_processer(writing_tasks)

    def _generate_writing_task(self, query, research_executions):
        """Generate structured writing tasks from research results."""
        write_agent = GenerateSubTaskAgent(
            self.model_config,
            self.mcp_tools_config_path,
            self.mcp_client_timeout,
            self.report_plan_parser,
        )
        research_info = self._construct_research_info(research_executions)
        research_plans = self._construct_research_plans(research_executions)
        writing_tasks: List[WritingTask] = yield from write_agent.run(
            query=query,
            context=dict(
                search_info=research_info,
                search_plan=research_plans
            )
        )
        for i, each in enumerate(writing_tasks):
            each.writing_need_info = research_info
            each.writing_task_id = f"section_{i + 1}"
        return writing_tasks

    def _write_task(self, query, writing_task: WritingTask) -> Generator[Message, None, str]:
        """Execute an individual writing task."""
        write_agent = ExecuteSubTaskAgent(self.model_config, self.mcp_tools_config_path, self.mcp_client_timeout)
        report = yield from write_agent.run(
            query=query,
            context=dict(
                write_instruction=writing_task.writing_instruction,
                search_info=writing_task.writing_need_info,
                write_task_id=writing_task.writing_task_id,
            )
        )
        return report

    def _construct_research_info(self, research_executions: List[ResearchExecution]) -> str:
        """Compile consolidated research information from executions."""
        result = ""
        for research_execution in research_executions:
            result += f"Research plan: {research_execution.plan.origin_plan}\n"
            result += f"Research result: \n"
            for step in research_execution.steps:
                result += f"Tool call: {step.tool_calls}\n"
                result += f"Conclusion: {step.content}\n"
        return result

    def _construct_research_plans(self, research_executions) -> str:
        """Extract original research plans from executions."""
        result = ""
        for research_execution in research_executions:
            result += f"{research_execution.plan.origin_plan}\n"
        return result

    def _default_report_plan_parser(self, full_response: str) -> List[WritingTask]:
        """
        Default parser for converting LLM responses to writing tasks.

        Args:
            full_response: Raw LLM response containing task definitions

        Returns:
            List[WritingTask]: Parsed writing tasks

        Note:
            Expects response to contain <result>...</result> blocks with task definitions
        """
        pattern = r'<result>(.*?)</result>'
        plan_reg_search = re.search(pattern, full_response, re.DOTALL)
        if not plan_reg_search:
            logging.error(f"Parse plan from <result> tag failed, origin llm text is {full_response}")
            plan_content = full_response
        else:
            plan_content = plan_reg_search.group(1)
        steps = plan_content.split("\n\n")
        result = []
        steps = [step.strip() for step in steps]
        for step in steps:
            step = step.strip()
            if step:
                result.append(WritingTask(
                    writing_instruction=step
                ))
        return result

    def _default_report_post_processer(self, writing_results: List[WritingTask]) -> str:
        """
        Default implementation for assembling final report from sections.

        Args:
            writing_results: List of completed writing tasks

        Returns:
            str: Formatted final report with citations and references
        """
        cleaned_text, citation_list = _process_citations("\n".join([each.writing_content for each in writing_results]))
        citation_dict, total_duplicate_map = _get_citation_dicts(citation_list)
        return _process_final_result(cleaned_text, total_duplicate_map, citation_dict)


def _process_citations(text, mode="both"):
    """
    Process citation blocks in text with multiple modes.

    Args:
        text: Input text containing citation blocks
        mode: Processing mode ("remove", "extract", or "both")

    Returns:
        Depending on mode:
        - "remove": Text with citations removed
        - "extract": List of citation blocks
        - "both": Tuple of (cleaned_text, citation_blocks)
    """
    start_tag = "[citation]"
    end_tag = "[citation/]"
    cleaned_text = text
    citation_blocks = []

    while True:
        # 找到 [citation] 的起始位置
        start_idx = cleaned_text.find(start_tag)
        if start_idx == -1:
            break  # 没有找到 [citation]，退出循环

        # 找到 [citation/] 的结束位置
        end_idx = cleaned_text.find(end_tag, start_idx)
        if end_idx == -1:
            break  # 没有找到 [citation/]，退出循环

        # 计算 citation 块的结束位置（包括 [citation/]）
        end_idx += len(end_tag)

        # 提取 citation 块
        citation_block = cleaned_text[start_idx:end_idx]
        citation_blocks.append(citation_block)

        # 从 cleaned_text 中删除 citation 块
        cleaned_text = cleaned_text[:start_idx] + cleaned_text[end_idx:]

    # 根据 mode 返回不同的结果
    if mode == "remove":
        return cleaned_text
    elif mode == "extract":
        return citation_blocks
    elif mode == "both":
        return cleaned_text, citation_blocks
    else:
        raise ValueError("Invalid mode. Use 'remove', 'extract', or 'both'.")


def _get_citation_dicts(citation_list):
    """
    Process citation blocks into structured dictionaries.

    Args:
        citation_list: List of raw citation blocks

    Returns:
        Tuple containing:
        - citation_dict: Mapping of citation keys to full content
        - total_duplicate_map: Mapping of URLs to citation metadata
    """
    total_duplicate_map = dict()
    # 打印结果
    citation_dict = dict()
    duplicate_count = 0
    for citation_text in citation_list:
        # 引用块
        if not citation_text:
            continue
        lines = citation_text.split('\n')
        for line in lines:
            line = line.strip()
            if line == "[citation]" or line == "[citation/]":
                continue
            if not line or '[' not in line:
                continue
            result = line.split(']')[0].split('[')[1]
            citation_dict[result] = line
            url, cut_line = _get_url_cite_content(line, result)
            if not url:
                continue
            if url not in total_duplicate_map:
                duplicate_count += 1
                total_duplicate_map[url] = {"index": str(duplicate_count), "citation_content": cut_line}
    return citation_dict, total_duplicate_map


def _get_url_cite_content(line, result):
    """
    Extract URL and formatted citation from a citation line.

    Args:
        line: Full citation line
        citation_key: Extracted citation key

    Returns:
        Tuple containing:
        - Extracted URL (or None if not found)
        - Formatted citation content
    """
    cut_length = len(result) + 2
    cut_line = line[cut_length:]
    pattern = r'\((.*?)\)'
    match = re.search(pattern, cut_line)
    if not match:
        return "", cut_line
    url = match.group(1)
    return url, cut_line


def _process_final_result(cleaned_text, total_duplicate_map, citation_dict):
    """
    Assemble final report with formatted citations and references.

    Args:
        cleaned_text: Main content with citation placeholders
        citation_map: Processed citation metadata
        citation_dict: Raw citation content

    Returns:
        str: Complete report with formatted citations and references section
    """
    # 得到替换后文档
    for key in citation_dict:
        if key not in citation_dict:
            raise ValueError("wrong citation structure")
        citation_val = citation_dict[key]
        result = citation_val.split(']')[0].split('[')[1]
        url, _ = _get_url_cite_content(citation_val, result)
        if url not in total_duplicate_map:
            logging.error(f"url not in total_duplication_map! the url is {url}")
            continue
        cleaned_text = cleaned_text.replace("[" + key + "]",
                                            "<sup>[" + total_duplicate_map[url].get("index") + "]</sup>")

    # 得到替换后参考文献
    citation = "## 参考内容  \n"
    for key, val in total_duplicate_map.items():
        citation += f"[{val.get('index')}]{val.get('citation_content')}  \n"

    return cleaned_text + "\n" + citation
