import json
import logging
import asyncio
from typing import List, Dict

from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from deepinsight.core.utils.tool_utils import create_retrieval_tool, CoerceToolOutput
from deepinsight.core.types.graph_config import RetrievalType
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.utils.db_schema_utils import get_db_models_source_markdown
from deepinsight.utils.tavily_manager import tavily_key_manager


# ----------------- 单篇论文解析函数 -----------------

async def analyze_single_paper(paper_info: str, output_dir: str, config: RunnableConfig) -> bool:
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
        tools = rc.file_system.tools()

        tavily_instance = tavily_key_manager().tool(
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
        
        # Add all configured retrieval tools
        if rc.retrieval_config:
            has_retrieval = False
            for retrieval_type in rc.retrieval_config.keys():
                try:
                    retrieval_tool = create_retrieval_tool(retrieval_type, config)
                    tools.append(retrieval_tool)
                    has_retrieval = True
                except Exception as e:
                    logging.warning(f"Failed to create retrieval tool for {retrieval_type}: {e}")
            
            if has_retrieval:
                prompt_template = rc.prompt_manager.get_prompt(
                    name="paper_analysis_prompt",
                    group=rc.prompt_group,
                ).format(output_dir=output_dir, db_models_description=get_db_models_source_markdown())

        from deepagents import create_deep_agent
        # Create the deep agent
        summary_subagent_system_prompt = rc.prompt_manager.get_prompt(
            name="review_paper_prompt",
            group=rc.prompt_group,
        ).format()
        summary_subagent = {
            "name": "summary-agent",
            "description": "顶会优秀论文分析与点评助手，你需要获取全部论文相关资料（必须包括1、主题与作者信息（200字）2、问题与挑战（300字），3、关键技术及技术效果（600字）,请注意这些内容是你需要预先获取的！！不是你要生成的任务）；通过这些论文资料分析并给出核心价值，并从技术创新性、理论贡献及商业落地角度分析与点评内容。",
            "system_prompt": summary_subagent_system_prompt,
            "tools": []
        }
        middleware = [
            CoerceToolOutput(),
            ModelFallbackMiddleware(
                rc.default_model,  # Try first on error
                rc.default_model,  # Then this
            )
        ]
        agent = create_deep_agent(
            model=rc.default_model,
            tools=tools,
            system_prompt=prompt_template,
            middleware=middleware,
            backend=rc.file_system.deep_agent_backend(),
            subagents=[summary_subagent]
        )
        input_messages = [
            {
                "role": "user",
                "content": f"请分析以下论文，并输出高质量结果：{paper_info}, 并将最终结果输出到目录：{output_dir},请注意你在生成todo list的时候，不要生成任意的Updated todo list to 内容"
            }]
        # Invoke the agent
        config_dict = dict(config) if not isinstance(config, dict) else config
        config_dict = {**config_dict, "recursion_limit": 500}
        config_dict = {**config_dict, "recursion_limit": 500}
        await agent.ainvoke({"messages": input_messages}, config=config_dict)
        return True
    except Exception as e:
        logging.error(f"论文解析失败: {paper_info}, 错误: {e}")
        import traceback
        traceback.print_exc()  # 打印堆栈信息
        return False  # 明确返回 False 表示失败


# ----------------- 批量论文解析工具 -----------------
@tool
async def batch_analyze_papers(
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
    
    # Create tasks for all papers
    tasks = [
        analyze_single_paper(paper, output_dir, config)
        for paper in papers_info
    ]
    
    # Execute tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    for paper, result in zip(papers_info, results):
        if isinstance(result, Exception):
            logging.error(f"Failed to analyze paper {paper}: {result}")
            result_map[paper] = False
        else:
            result_map[paper] = True # analyze_single_paper returns True on success (if we add return True)
            
    return result_map
