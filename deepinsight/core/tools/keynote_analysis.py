import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable
from typing import List, Dict

from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_tavily import TavilySearch

from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.tools.file_system import register_fs_tools, fs_instance


import os
from langchain.tools import tool
from langchain_tavily import TavilySearch


@tool
def person_image_search_tool(person_name: str, person_background: str, config: RunnableConfig) -> str:
    """Tool to search for an image of a person based on their name and background information."""

    # 输入参数:
    # person_name (str): 人员的姓名。例如："John Doe"。
    # person_background (str): 人员的背景信息，通常包括其工作地和从事的行业或领域。
    #                          例如："Harvard University, Professor of Physics"。

    rc = parse_research_config(config)

    tool_instance = TavilySearch(
        max_results=10,
        topic="general",
        include_answer=True,
        include_raw_content=False,
        include_images=True,
        include_image_descriptions=False,
        search_depth="advanced"
    )

    from langchain.agents import create_agent
    user_prompt = f"""
    帮我搜索找下如下人员的头像或生活照：人名：{person_name} 背景信息：{person_background}，输出格式参考如下(不要输出其它任何内容，仅输出json)： 
    {{
        "name": "<人员姓名>",  # 人员的姓名
        "image": "<图片URL>"  # 图片链接，可以是头像照片或生活照的URL
    }}
    """

    agent = create_agent(rc.default_model, tools=[tool_instance])
    input_messages = [
        {
            "role": "user",
            "content": user_prompt
        }]
    result = agent.invoke({"messages": input_messages})
    result_text = result["messages"][-1].content
    return result_text


# ----------------- 单篇论文解析函数 -----------------

def analyze_single_keynote(keynote_info: str, output_dir: str, config: RunnableConfig) -> bool:
    """
    对keynotes进行解析，并将结果保存到文件

    Args:
        keynote_info: keynotes信息
        output_dir: 保存解析结果的文件夹路径

    Returns:
        bool: True表示解析成功并保存，False表示解析失败
    """
    try:
        print(f"begin to analyze_single_keynote: {keynote_info}")
        rc = parse_research_config(config)
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
        # Step 2: Generate structured research brief from user messages
        prompt_content = rc.prompt_manager.get_prompt(
            name="analyze_keynote_system_prompt",
            group=rc.prompt_group,
        ).format(output_dir=output_dir)

        tools.append(tavily_instance)
        tools.append(person_image_search_tool)
        from deepagents import create_deep_agent
        # Create the deep agent
        agent = create_deep_agent(
            model=rc.default_model,
            tools=tools,
            instructions=prompt_content,
        )
        input_messages = [
            {
                "role": "user",
                "content": f"请分析以下学术会议的keyote，并输出高质量结果, keynote信息：{keynote_info}, 并将最终结果输出到目录：{output_dir}"
            }]
        print(f"tools:{tools}")
        # Invoke the agent
        config_dict = dict(config) if not isinstance(config, dict) else config
        config_dict = {**config_dict, "recursion_limit": 300}

        result = agent.invoke({"messages": input_messages}, config=config_dict)
    except Exception as e:
        logging.error(f"keynote分析失败: {keynote_info}, 错误: {e}")
        import traceback
        traceback.print_exc()  # 打印堆栈信息


# ----------------- 批量论文解析工具 -----------------
@tool
def batch_analyze_keynotes(
    keynotes_info: List[str],
    output_dir: str,
    config: RunnableConfig
) -> Dict[str, bool]:
    """
    批量并行分析 keynotes，将分析后的结果保存到指定文件夹中，
    并返回每篇论文是否分析成功的 Map。

    Args:
        keynotes_info (List[str]):
            每个元素是一个 keynotes 的的自然语言描述或 JSON字符串,不能是json格式
            示例：
            [
                '{"keynote_name": "AI for the Future of Computing", "speaker": "Dr. Jane Doe", "conference": "ICCAD 2025"}',
                '{"keynote_name": "Semiconductor Innovation in the Post-Moore Era", "speaker": "Prof. John Smith", "conference": "ICCAD 2025"}'
            ]
        output_dir (str): 保存 keynotes 分析结果的文件夹路径
    Returns:
        Dict[str, bool]: 每个 keynote 对应的分析成功状态(True/False)
    """
    logging.info(f"接收到 keynotes_info，共 {len(keynotes_info)} 个")
    logging.info(f"输出路径: {output_dir}")
    logging.info(f"执行配置: {config}")

    result_map: Dict[str, bool] = {}
    timeout_seconds = 15 * 60  # 15 分钟超时

    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_keynote = {
            executor.submit(analyze_single_keynote, keynote_str, output_dir, config): keynote_str
            for keynote_str in keynotes_info
        }

        for future in as_completed(future_to_keynote, timeout=timeout_seconds):
            keynote_str = future_to_keynote[future]
            try:
                success = future.result()
                result_map[keynote_str] = success
                logging.info(f"分析完成: {keynote_str[:80]}... -> {success}")
            except Exception as e:
                result_map[keynote_str] = False
                logging.error(f"分析失败: {keynote_str[:80]}... 错误: {e}", exc_info=True)

    return result_map
