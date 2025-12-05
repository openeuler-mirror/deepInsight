import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_tavily import TavilySearch

from deepinsight.core.tools.file_system import register_fs_tools, MemoryMCPFilesystem
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.utils.db_schema_utils import get_db_models_source_markdown
from deepinsight.core.utils.context_utils import DefaultSummarizationMiddleware

# ----------------- 单篇论文解析函数 -----------------

def analyze_single_paper(paper_info: str, output_dir: str, config: RunnableConfig) -> bool:
    """
    对单篇论文进行解析，并将结果保存到文件

    Args:
        paper_info: 单篇论文信息，内容包括：论文标题，作者，来源，优秀基因等
        output_dir: 保存解析结果的文件夹路径

    Returns:
        bool: True表示解析成功并保存，False表示解析失败
    """
    try:
        rc = parse_research_config(config)
        fs_instance = MemoryMCPFilesystem()
        tools = register_fs_tools(fs_instance)

        tavily_instance = TavilySearch(
            max_results=2,
            topic="general",
            include_answer=True,
            include_raw_content=False,
            include_images=False,
            include_image_descriptions=True,
            search_depth="advanced",
        )
        prompt_template = rc.prompt_manager.get_prompt(
                name="paper_analysis_no_rag_prompt",
                group=rc.prompt_group,
        ).format(output_dir=output_dir, db_models_description=get_db_models_source_markdown())

        tools.append(tavily_instance)
        if "ragflow" in config["configurable"]:
            # knowledge_tool = KnowledgeTool()
            # tools.append(knowledge_tool.knowledge_retrieve)
            prompt_template = rc.prompt_manager.get_prompt(
                name="paper_analysis_prompt",
                group=rc.prompt_group,
            ).format(output_dir=output_dir, db_models_description=get_db_models_source_markdown())

        from deepagents import create_deep_agent
        # Create the deep agent

        agent = create_deep_agent(
            model=rc.default_model,
            tools=tools,
            system_prompt=prompt_template,
        )
        input_messages = [
            {
                "role": "user",
                "content": f"请分析以下论文，并输出高质量结果：{paper_info}, 并将最终结果输出到目录：{output_dir},请注意你在生成todo list的时候，不要生成任意的Updated todo list to 内容"
            }]
        # Invoke the agent
        config_dict = dict(config) if not isinstance(config, dict) else config
        config_dict = {**config_dict, "recursion_limit": 500}
        agent.invoke({"messages": input_messages}, config=config_dict)
    except Exception as e:
        logging.error(f"论文解析失败: {paper_info}, 错误: {e}")
        import traceback
        traceback.print_exc()  # 打印堆栈信息


# ----------------- 批量论文解析工具 -----------------
@tool
def batch_analyze_papers(
        papers_info: List[str],
        output_dir: str,
        config: RunnableConfig
) -> Dict[str, bool]:
    """
    批量并行分析论文，将分析后的结果保存到指定的文件系统中，返回每篇论文是否分析成功 Map

    Args:
        papers_info: 论文集合相关信息，每条论文信息包含：论文标题，作者，来源，优秀基因等，例如 ["论文A信息", "论文B信息"]
        output_dir: 保存解析结果的文件夹路径

    Returns:
        Dict[str, bool]: 每篇论文标题对应的分析成功状态(True/False)
    """
    logging.info(f"paper_titles: {papers_info}")
    logging.info(f"output_dir: {output_dir}")
    result_map = {}
    timeout_seconds = 15 * 60  # 15 分钟
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_paper = {executor.submit(analyze_single_paper, paper, output_dir, config): paper for paper in
                           papers_info}
        for future in as_completed(future_to_paper, timeout=timeout_seconds):
            paper = future_to_paper[future]
            result_map[paper] = True
    return result_map
