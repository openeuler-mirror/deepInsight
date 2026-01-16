"""
跨会议主题分析 Supervisor Graph

使用 LangGraph 将各个步骤串起来：
1. 收集论文列表
2. 生成统计信息
3. 批量分析论文
4. 生成总结
5. 存盘
"""
import logging
import os
import asyncio
from enum import Enum
from typing import Annotated, List, Dict, Optional
from pydantic import BaseModel, Field

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.constants import END
from langgraph.graph import StateGraph, add_messages
from deepinsight.core.utils.progress_utils import progress_stage

from deepinsight.core.agent.conf_gen.cross_topic_paper_collection import collect_papers_for_topic
from deepinsight.core.tools.best_paper_analysis import batch_analyze_papers, analyze_single_paper
from deepinsight.core.tools.file_system import register_fs_tools, MemoryMCPFilesystem, fs_instance
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.graph_config import ResearchConfig
from deepinsight.core.types.research import FinalResult
from deepinsight.core.types.conference_constants import (
    ConferenceFolderNames,
    ConferenceFileNames,
)
from deepinsight.core.agent.resch_gen.supervisor import graph as deep_research_graph
from deepinsight.core.types.graph_config import SearchAPI

logger = logging.getLogger(__name__)


class CrossTopicGraphNodeType(str, Enum):
    """跨会议主题分析图的节点类型"""
    COLLECT_PAPERS = "collect_papers"
    GENERATE_STATISTICS = "generate_statistics"
    ANALYZE_PAPERS = "analyze_papers"
    GENERATE_SUMMARY = "generate_summary"
    SAVE_FILES = "save_files"

    def __str__(self):
        return self.value


class CrossTopicState(dict):
    """跨会议主题分析的状态"""
    messages: Annotated[List[BaseMessage], add_messages]
    question: str
    # kb_ids 从 config.retrieval_config 中获取，不存储在 state 中
    papers: List[Dict]
    papers_list_file: str
    statistics_content: str
    statistics_file: str
    papers_analysis_dir: str
    summary_content: str
    summary_file: str
    output_path: str

    def __init__(self, **kwargs):
        defaults = {
            "messages": [],
            "question": "",
            "papers": [],
            "papers_list_file": "",
            "statistics_content": "",
            "statistics_file": "",
            "papers_analysis_dir": "",
            "summary_content": "",
            "summary_file": "",
            "output_path": "",
        }
        super().__init__({**defaults, **kwargs})


async def construct_sub_config(config, prompt_group: str):
    """构建子图配置"""
    return {
        **config.get("configurable", {}),
        "prompt_group": prompt_group,
        "allow_user_clarification": False,
        "allow_edit_research_brief": False,
        "allow_edit_report_outline": False,
        "allow_publish_result": False,
        "tools": [],
        "search_api": [SearchAPI.TAVILY],
    }


@progress_stage("收集论文列表")
async def collect_papers_node(state: CrossTopicState, config: RunnableConfig):
    """收集论文列表节点"""
    from deepinsight.core.types.graph_config import RetrievalType
    
    rc = parse_research_config(config)
    papers_output_file = f"/{rc.run_id}/papers_list.md"
    
    # 从 config 的 retrieval_config 中获取 kb_ids（与 deep_research_team_node 保持一致）
    # 注意：web 版本的 kb_ids 可能是字符串（UUID），CLI 版本是整数
    kb_ids = []
    if rc.retrieval_config:
        for retrieval_type, retrieval_config in rc.retrieval_config.items():
            if hasattr(retrieval_config, 'args') and hasattr(retrieval_config.args, 'kb_ids'):
                # 保持原始类型，不强制转换为 int（web 版本可能是字符串）
                raw_kb_ids = retrieval_config.args.kb_ids or []
                kb_ids.extend(raw_kb_ids)
    
    if not kb_ids:
        error_msg = (
            f"无法从配置中获取知识库信息。\n"
            f"请确保 config.configurable.retrieval_config 中包含有效的 kb_ids。"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info(f"从 retrieval_config 中获取到 {len(kb_ids)} 个知识库ID: {kb_ids}")
    
    # 尝试从 config 中获取 conference_names（CLI 版本可能提供）
    conference_names = None
    if hasattr(config, 'get') and config.get("configurable"):
        configurable = config.get("configurable", {})
        if "conference_names" in configurable:
            conference_names = configurable["conference_names"]
            logger.info(f"从 config 中获取到会议名称列表: {conference_names}")
    
    # 如果没有提供，尝试从关系数据库查询（仅 CLI 版本，可选）
    # 注意：web 版本的 kb_ids 可能是字符串，无法直接查询 KnowledgeBase 表
    if conference_names is None:
        try:
            from deepinsight.databases.connection import Database
            from deepinsight.databases.models.knowledge import KnowledgeBase
            from deepinsight.databases.models.academic import Conference
            
            # 尝试将 kb_ids 转换为整数（仅 CLI 版本）
            # web 版本的 kb_ids 是字符串（UUID），无法查询 KnowledgeBase 表
            try:
                kb_ids_int = [int(kb_id) for kb_id in kb_ids]
            except (ValueError, TypeError):
                # 如果无法转换为整数，说明是 web 版本，跳过数据库查询
                logger.debug(f"kb_ids 包含非整数格式（可能是 web 版本）: {kb_ids}，跳过 KnowledgeBase 查询")
                conference_names = None
            else:
                # 只有 CLI 版本（整数 kb_ids）才查询 KnowledgeBase 表
                with Database().get_session() as session:
                    kbs = session.query(KnowledgeBase).filter(
                        KnowledgeBase.kb_id.in_(kb_ids_int),
                        KnowledgeBase.owner_type == "conference",
                        KnowledgeBase.owner_id.is_not(None)
                    ).all()
                    conference_ids = [kb.owner_id for kb in kbs if kb.owner_id]
                    
                    if conference_ids:
                        conferences = session.query(Conference).filter(
                            Conference.conference_id.in_(conference_ids)
                        ).all()
                        # 构建会议名称列表，格式如 "HOTOS 2025"
                        conference_names = [
                            f"{c.short_name or c.full_name} {c.year}" 
                            for c in conferences
                        ]
                        logger.info(f"从数据库查询到会议名称列表: {conference_names}")
        except Exception as e:
            # Web 版本可能没有 KnowledgeBase 表，忽略错误
            logger.debug(f"无法从数据库查询会议名称（可能是 web 版本）: {e}")
            conference_names = None
    
    # 兼容直接从 ResearchService 调用（只有 messages，没有显式 question 字段）的情况
    question = state.get("question")
    if not question:
        try:
            # 从最后一条用户消息中提取文本
            last_msg = state.get("messages", [])[-1]
            if isinstance(last_msg, tuple):
                # 形如 ("user", text)
                question = last_msg[1]
            elif hasattr(last_msg, "content"):
                question = last_msg.content
        except Exception:
            question = ""
        state["question"] = question

    try:
        papers = await collect_papers_for_topic(
            question=question,
            kb_ids=kb_ids,
            output_file=papers_output_file,
            config=config,
            conference_names=conference_names,  # 传递会议名称列表
        )
        
        if not papers:
            # 检查文件是否存在（使用单例实例）
            from deepinsight.core.tools.file_system import fs_instance
            content = fs_instance.read_file(papers_output_file)
            if not content:
                error_msg = (
                    f"未找到相关论文。可能的原因：\n"
                    f"1. 数据库中可能没有与主题 '{state.get('question', '')}' 相关的论文\n"
                    f"2. Agent 未能成功生成论文列表文件\n"
                    f"3. 知识库可能未正确解析（知识库ID: {kb_ids}）\n"
                    f"请检查：\n"
                    f"- 知识库数据是否正确解析\n"
                    f"- 数据库中是否有相关论文\n"
                    f"- 主题关键词是否准确"
                )
            else:
                error_msg = (
                    f"未找到相关论文。Agent 生成了文件但解析失败。\n"
                    f"文件内容预览: {content[:500]}\n"
                    f"请检查文件格式是否符合要求（JSON 或 Markdown 格式）"
                )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info(f"收集到 {len(papers)} 篇论文")
        
        # 确保后续节点始终可以访问到 question 字段
        return {
            "papers": papers,
            "papers_list_file": papers_output_file,
            "question": state.get("question", question),
        }
    except Exception as e:
        logger.exception(f"收集论文列表时出错: {e}")
        raise


@progress_stage("生成统计信息")
async def generate_statistics_node(state: CrossTopicState, config: RunnableConfig):
    """生成统计信息节点"""
    from deepagents import create_deep_agent
    from langchain.agents.middleware import ModelFallbackMiddleware
    from langchain_tavily import TavilySearch
    from deepinsight.core.tools.file_system import register_fs_tools, MemoryMCPFilesystem
    from deepinsight.core.utils.tool_utils import CoerceToolOutput
    from langfuse.langchain import CallbackHandler
    
    rc = parse_research_config(config)

    # 兼容没有显式 question 的情况，从 messages 中推断
    question = state.get("question")
    if not question:
        try:
            last_msg = state.get("messages", [])[-1]
            if isinstance(last_msg, tuple):
                question = last_msg[1]
            elif hasattr(last_msg, "content"):
                question = last_msg.content
        except Exception:
            question = ""
        state["question"] = question
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
    tools.append(tavily_instance)
    
    # 构建论文信息字符串（会议信息从用户问题中获取）
    papers_info = "\n".join([
        f"- {p.get('title', 'Unknown')} ({p.get('conference', 'Unknown')} {p.get('year', '')})"
        for p in state["papers"]
    ])
    
    # 获取prompt（使用用户问题代替结构化会议信息）
    prompt_template = rc.prompt_manager.get_prompt(
        name="cross_topic_statistics_prompt",
        group=rc.prompt_group,
    ).format(
        question=state.get("question", question),
        papers_count=len(state["papers"]),
        papers_info=papers_info,
    )
    
    output_file = f"/{rc.run_id}/{ConferenceFileNames.CROSS_TOPIC_STATISTICS_MD}"
    
    middleware = [
        CoerceToolOutput(),
        ModelFallbackMiddleware(
            rc.default_model,
            rc.default_model,
        )
    ]
    
    agent = create_deep_agent(
        model=rc.default_model,
        tools=tools,
        system_prompt=prompt_template,
        middleware=middleware,
    )
    
    user_message = f"请生成跨会议主题分析的统计信息报告，保存到：{output_file}"
    
    # 添加 Langfuse 追踪
    # 使用 .with_config 方式传递 callbacks（类似 ror.py 中的用法）
    langfuse_handler = CallbackHandler()
    config_dict = dict(config) if not isinstance(config, dict) else config
    config_dict = {**config_dict, "recursion_limit": 300}
    
    # 尝试使用 .with_config 方式传递 callbacks
    try:
        agent_with_callbacks = agent.with_config(
            run_name="generate_statistics",
            callbacks=[langfuse_handler]
        )
        await agent_with_callbacks.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
    except (AttributeError, TypeError) as e:
        # 如果 with_config 不支持，尝试直接传递（可能不兼容）
        logger.warning(f"无法使用 with_config 传递 callbacks: {e}，尝试直接传递")
        try:
            if "callbacks" not in config_dict:
                config_dict["callbacks"] = []
            elif not isinstance(config_dict["callbacks"], list):
                config_dict["callbacks"] = [config_dict["callbacks"]]
            config_dict["callbacks"].append(langfuse_handler)
            await agent.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
        except Exception as e2:
            logger.warning(f"无法添加 Langfuse 追踪: {e2}，跳过追踪继续执行")
            await agent.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
    
    # 读取生成的文件
    statistics_content = fs_instance.read_file(output_file)
    
    return {
        "statistics_content": statistics_content,
        "statistics_file": output_file,
    }


@progress_stage("批量分析论文")
async def analyze_papers_node(state: CrossTopicState, config: RunnableConfig):
    """批量分析论文节点"""
    rc = parse_research_config(config)
    papers_output_dir = f"/{rc.run_id}/{ConferenceFolderNames.CROSS_TOPIC_PAPERS}"
    
    # 确保输出目录存在（在内存文件系统中）
    fs_instance = MemoryMCPFilesystem()
    fs_instance._ensure_dir_exists(papers_output_dir)
    logger.info(f"确保论文输出目录存在: {papers_output_dir}")
    
    # 准备论文信息列表，过滤无效条目并去重
    invalid_keywords = ['概述', '论文列表', '统计信息', '主题分布', 'Overview', 'Paper List', 'Statistics', 'Topic Distribution', 'List', 'Summary']
    valid_papers = []
    seen_titles = set()  # 用于去重（基于标题，不区分大小写）
    
    for p in state["papers"]:
        title = p.get('title', '').strip()
        if not title:
            continue
        title_lower = title.lower()
        
        # 检查是否包含无效关键词
        if any(keyword.lower() in title_lower for keyword in invalid_keywords):
            logger.debug(f"跳过无效论文条目: {title}")
            continue
        
        # 检查是否包含有效的论文信息
        if not (p.get('authors') or p.get('conference')):
            logger.debug(f"跳过信息不完整的论文条目: {title}")
            continue
        
        # 去重：基于标题（不区分大小写）
        if title_lower in seen_titles:
            logger.warning(f"跳过重复论文: {title} (已存在)")
            continue
        
        seen_titles.add(title_lower)
        valid_papers.append(p)
    
    if not valid_papers:
        logger.warning("过滤后没有有效的论文，使用原始列表")
        valid_papers = state["papers"]
    
    logger.info(f"去重后有效论文数量: {len(valid_papers)} (原始数量: {len(state['papers'])})")
    
    paper_info_list = [
        f"标题: {p.get('title', 'Unknown')}\n"
        f"作者: {p.get('authors', 'Unknown')}\n"
        f"会议: {p.get('conference', 'Unknown')} {p.get('year', '')}\n"
        f"摘要: {p.get('abstract', '')[:200]}..."
        for p in valid_papers
    ]
    
    # 批量分析论文
    # batch_analyze_papers 是一个 @tool 装饰的工具，需要访问其原始函数
    # 使用 .coroutine 属性来获取原始协程函数
    if hasattr(batch_analyze_papers, 'coroutine'):
        # 使用工具的协程函数（原始函数）
        result_map = await batch_analyze_papers.coroutine(
            papers_info=paper_info_list,
            output_dir=papers_output_dir,
            config=config
        )
        # 记录分析结果
        success_count = sum(1 for success in result_map.values() if success)
        failed_count = len(result_map) - success_count
        logger.info(f"论文分析完成: 成功 {success_count} 篇, 失败 {failed_count} 篇")
        if failed_count > 0:
            failed_papers = [paper[:50] + "..." if len(paper) > 50 else paper 
                           for paper, success in result_map.items() if not success]
            logger.warning(f"分析失败的论文 ({failed_count} 篇): {failed_papers}")
    else:
        # 如果没有 coroutine 属性，直接并行调用 analyze_single_paper
        # 这是 batch_analyze_papers 内部实际做的事情
        import asyncio
        tasks = [
            analyze_single_paper(paper, papers_output_dir, config)
            for paper in paper_info_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 统计成功和失败的数量
        success_count = sum(1 for r in results if r is True)
        failed_count = len(results) - success_count
        logger.info(f"论文分析完成: 成功 {success_count} 篇, 失败 {failed_count} 篇")
        if failed_count > 0:
            failed_papers = [paper_info_list[i][:50] + "..." if len(paper_info_list[i]) > 50 else paper_info_list[i]
                           for i, r in enumerate(results) if not (r is True)]
            logger.warning(f"分析失败的论文 ({failed_count} 篇): {failed_papers}")
    
    return {
        "papers_analysis_dir": papers_output_dir,
    }


@progress_stage("生成总结")
async def generate_summary_node(state: CrossTopicState, config: RunnableConfig):
    """生成总结节点"""
    from deepagents import create_deep_agent
    from langchain.agents.middleware import ModelFallbackMiddleware
    from langchain_tavily import TavilySearch
    from deepinsight.core.tools.file_system import register_fs_tools, MemoryMCPFilesystem
    from deepinsight.core.utils.tool_utils import CoerceToolOutput
    from langfuse.langchain import CallbackHandler
    
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
    tools.append(tavily_instance)
    
    # 读取所有论文分析文件
    paper_files_content_map = fs_instance.read_all_files_in_dir(state["papers_analysis_dir"])
    papers_content = "\n\n".join(content for _, content in paper_files_content_map.items())
    
    # 兼容没有显式 question 的情况，从 state 或 messages 中推断
    question = state.get("question")
    if not question:
        try:
            last_msg = state.get("messages", [])[-1]
            if isinstance(last_msg, tuple):
                question = last_msg[1]
            elif hasattr(last_msg, "content"):
                question = last_msg.content
        except Exception:
            question = ""
        state["question"] = question

    # 获取prompt
    prompt_template = rc.prompt_manager.get_prompt(
        name="cross_topic_summary_prompt",
        group=rc.prompt_group,
    ).format(
        question=state.get("question", question),
        statistics_content=state["statistics_content"],
        papers_content=papers_content,
    )
    
    output_file = f"/{rc.run_id}/{ConferenceFileNames.CROSS_TOPIC_SUMMARY_MD}"
    
    middleware = [
        CoerceToolOutput(),
        ModelFallbackMiddleware(
            rc.default_model,
            rc.default_model,
        )
    ]
    
    agent = create_deep_agent(
        model=rc.default_model,
        tools=tools,
        system_prompt=prompt_template,
        middleware=middleware,
    )
    
    user_message = f"请生成跨会议主题分析的总结报告，保存到：{output_file}"
    
    # 添加 Langfuse 追踪
    # 使用 .with_config 方式传递 callbacks（类似 ror.py 中的用法）
    langfuse_handler = CallbackHandler()
    config_dict = dict(config) if not isinstance(config, dict) else config
    config_dict = {**config_dict, "recursion_limit": 300}
    
    # 尝试使用 .with_config 方式传递 callbacks
    try:
        agent_with_callbacks = agent.with_config(
            run_name="generate_summary",
            callbacks=[langfuse_handler]
        )
        await agent_with_callbacks.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
    except (AttributeError, TypeError) as e:
        # 如果 with_config 不支持，尝试直接传递（可能不兼容）
        logger.warning(f"无法使用 with_config 传递 callbacks: {e}，尝试直接传递")
        try:
            if "callbacks" not in config_dict:
                config_dict["callbacks"] = []
            elif not isinstance(config_dict["callbacks"], list):
                config_dict["callbacks"] = [config_dict["callbacks"]]
            config_dict["callbacks"].append(langfuse_handler)
            await agent.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
        except Exception as e2:
            logger.warning(f"无法添加 Langfuse 追踪: {e2}，跳过追踪继续执行")
            await agent.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
    
    # 读取生成的文件
    summary_content = fs_instance.read_file(output_file)
    
    return {
        "summary_content": summary_content,
        "summary_file": output_file,
    }


@progress_stage("保存文件到磁盘")
async def save_files_node(state: CrossTopicState, config: RunnableConfig):
    """保存文件到磁盘节点"""
    rc = parse_research_config(config)
    
    # 确定输出路径
    work_root = getattr(rc, "work_root", None)
    if not work_root:
        work_root = os.getcwd()
    thread_id = rc.thread_id
    output_path = os.path.join(work_root, "conference_report_result", thread_id)
    os.makedirs(output_path, exist_ok=True)
    
    # 导出内存文件系统到磁盘
    # 确保使用单例 fs_instance，并确保导出目录存在于内存文件系统中
    output_dir = f"/{rc.run_id}/"
    fs_instance._ensure_dir_exists(output_dir)  # 确保目录存在
    
    # 检查内存文件系统中的文件
    logger.info(f"内存文件系统中的目录: {fs_instance.dirs}")
    logger.info(f"内存文件系统中的文件: {list(fs_instance.files.keys())}")
    logger.info(f"准备导出目录: {output_dir}")
    
    # 检查是否有论文分析文件
    papers_dir_in_memory = f"{output_dir}{ConferenceFolderNames.CROSS_TOPIC_PAPERS}/"
    papers_files_in_memory = [f for f in fs_instance.files.keys() if f.startswith(papers_dir_in_memory)]
    logger.info(f"内存文件系统中的论文分析文件 ({len(papers_files_in_memory)} 个): {papers_files_in_memory[:5]}...")
    
    try:
        export_result = fs_instance.export_to_real_fs(real_dir=output_path, folder_path=output_dir)
        logger.info(f"文件导出结果: {export_result}")
    except Exception as e:
        logger.warning(f"导出内存文件系统失败: {e}，尝试手动保存文件")
        # 如果导出失败，手动保存关键文件
        if state.get("statistics_content"):
            statistics_file_path = os.path.join(output_path, ConferenceFileNames.CROSS_TOPIC_STATISTICS_MD)
            with open(statistics_file_path, 'w', encoding='utf-8') as f:
                f.write(state["statistics_content"])
        if state.get("summary_content"):
            summary_file_path = os.path.join(output_path, ConferenceFileNames.CROSS_TOPIC_SUMMARY_MD)
            with open(summary_file_path, 'w', encoding='utf-8') as f:
                f.write(state["summary_content"])
    
    # 确保统计信息和总结文件已保存（如果内存文件系统已导出，这些文件应该已经存在）
    # 如果不存在，手动保存
    statistics_file_path = os.path.join(output_path, ConferenceFileNames.CROSS_TOPIC_STATISTICS_MD)
    summary_file_path = os.path.join(output_path, ConferenceFileNames.CROSS_TOPIC_SUMMARY_MD)
    papers_list_file_path = os.path.join(output_path, "papers_list.md")
    papers_dir = os.path.join(output_path, ConferenceFolderNames.CROSS_TOPIC_PAPERS)
    
    # 检查导出后的文件结构
    logger.info(f"导出后的输出路径: {output_path}")
    logger.info(f"导出后的文件列表: {os.listdir(output_path) if os.path.exists(output_path) else '目录不存在'}")
    logger.info(f"论文分析目录路径: {papers_dir}")
    logger.info(f"论文分析目录是否存在: {os.path.exists(papers_dir)}")
    if os.path.exists(papers_dir):
        logger.info(f"论文分析目录中的文件: {os.listdir(papers_dir)}")
    
    if not os.path.exists(statistics_file_path) and state.get("statistics_content"):
        with open(statistics_file_path, 'w', encoding='utf-8') as f:
            f.write(state["statistics_content"])
        logger.info(f"手动保存统计信息文件: {statistics_file_path}")
    
    if not os.path.exists(summary_file_path) and state.get("summary_content"):
        with open(summary_file_path, 'w', encoding='utf-8') as f:
            f.write(state["summary_content"])
        logger.info(f"手动保存总结文件: {summary_file_path}")
    
    # 构建 PDF 内容：按照顺序合并所有内容
    # 1. 统计信息
    # 2. 论文分析（cross_topic_papers 目录中的内容）
    # 3. 总结
    # 4. 论文列表
    markdown_parts = []
    
    # 1. 添加统计信息
    if os.path.exists(statistics_file_path):
        with open(statistics_file_path, 'r', encoding='utf-8') as f:
            markdown_parts.append(f"# 统计信息\n\n{f.read()}")
    elif state.get("statistics_content"):
        markdown_parts.append(f"# 统计信息\n\n{state['statistics_content']}")
    
    # 2. 添加论文分析（按文件名排序）
    if os.path.exists(papers_dir):
        paper_files = sorted(
            [f for f in os.listdir(papers_dir) if f.endswith(".md")],
            key=lambda x: x.lower()
        )
        if paper_files:
            markdown_parts.append("\n\n# 论文分析\n\n")
            for paper_file in paper_files:
                paper_path = os.path.join(papers_dir, paper_file)
                with open(paper_path, 'r', encoding='utf-8') as f:
                    markdown_parts.append(f.read())
                    markdown_parts.append("\n\n---\n\n")  # 论文之间的分隔符
    
    # 3. 添加总结
    if os.path.exists(summary_file_path):
        with open(summary_file_path, 'r', encoding='utf-8') as f:
            markdown_parts.append(f"# 总结\n\n{f.read()}")
    elif state.get("summary_content"):
        markdown_parts.append(f"# 总结\n\n{state['summary_content']}")
    
    # 4. 添加论文列表（如果是 JSON 格式，转换为文本格式）
    if os.path.exists(papers_list_file_path):
        with open(papers_list_file_path, 'r', encoding='utf-8') as f:
            papers_list_content = f.read()
            # 检查是否是 JSON 格式
            try:
                import json
                papers_data = json.loads(papers_list_content.strip())
                if isinstance(papers_data, list):
                    # 转换为 Markdown 文本格式
                    papers_text_parts = []
                    for idx, paper in enumerate(papers_data, 1):
                        title = paper.get('title', 'Unknown Title')
                        authors = paper.get('authors', 'Unknown Authors')
                        conference = paper.get('conference', 'Unknown Conference')
                        year = paper.get('year', 'Unknown Year')
                        abstract = paper.get('abstract', '')
                        keywords = paper.get('keywords', '')
                        
                        paper_text = f"## {idx}. {title}\n\n"
                        paper_text += f"**作者**：{authors}\n\n"
                        paper_text += f"**会议**：{conference} {year}\n\n"
                        if abstract:
                            paper_text += f"**摘要**：{abstract}\n\n"
                        if keywords:
                            paper_text += f"**关键词**：{keywords}\n\n"
                        papers_text_parts.append(paper_text)
                    
                    papers_text = "\n".join(papers_text_parts)
                    markdown_parts.append(f"\n\n# 论文列表\n\n{papers_text}")
                else:
                    # 不是列表格式，直接使用原内容
                    markdown_parts.append(f"\n\n# 论文列表\n\n{papers_list_content}")
            except (json.JSONDecodeError, ValueError):
                # 不是 JSON 格式，直接使用原内容
                markdown_parts.append(f"\n\n# 论文列表\n\n{papers_list_content}")
    
    # 合并所有内容
    merged_markdown = "\n\n".join(markdown_parts)
    
    # 添加报告头部信息
    from datetime import datetime
    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日 %H时%M分%S秒")
    
    # 构建会议信息字符串
    # 会议信息从用户问题中获取，不需要单独列出
    question = state.get("question", "")
    header_info = (
        f"# 跨会议主题分析报告\n\n"
        f"**研究主题**：{question}\n\n"
        f"**生成时间**：{time_str}\n\n"
        f"---\n\n"
    )
    
    final_markdown = header_info + merged_markdown
    
    # 生成 PDF
    try:
        from deepinsight.utils.trans_md_to_pdf import save_markdown_as_pdf
        from datetime import datetime
        
        time_for_filename = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"cross_topic_report_{time_for_filename}.pdf"
        pdf_path = os.path.join(output_path, pdf_filename)
        
        # 异步执行 PDF 生成（避免阻塞）
        await asyncio.to_thread(
            save_markdown_as_pdf,
            markdown_content=final_markdown,
            output_filename=pdf_path,
            base_url=output_path,
        )
        logger.info(f"PDF 生成成功: {pdf_path}")
    except Exception as e:
        logger.warning(f"PDF 生成失败: {e}", exc_info=True)
    
    # 构建最终报告内容（用于流式输出）
    # 使用与 PDF 相同的内容结构，确保最终 Markdown 文件包含完整内容
    full_text = final_markdown
    
    # 输出最终结果
    writer = get_stream_writer()
    writer(FinalResult(
        final_report=full_text,
    ))
    
    # 将完整报告添加到 messages 中，以便 cross_topic_team_node 可以获取（类似 deep_research_team_node）
    from langchain_core.messages import HumanMessage
    return {
        "output_path": output_path,
        "pdf_path": pdf_path if 'pdf_path' in locals() else None,
        "messages": [HumanMessage(content=full_text)],
    }


# 构建图
builder = StateGraph(CrossTopicState)

# 注册节点
builder.add_node(CrossTopicGraphNodeType.COLLECT_PAPERS, collect_papers_node)
builder.add_node(CrossTopicGraphNodeType.GENERATE_STATISTICS, generate_statistics_node)
builder.add_node(CrossTopicGraphNodeType.ANALYZE_PAPERS, analyze_papers_node)
builder.add_node(CrossTopicGraphNodeType.GENERATE_SUMMARY, generate_summary_node)
builder.add_node(CrossTopicGraphNodeType.SAVE_FILES, save_files_node)

# 设置边
builder.set_entry_point(CrossTopicGraphNodeType.COLLECT_PAPERS)
builder.add_edge(CrossTopicGraphNodeType.COLLECT_PAPERS, CrossTopicGraphNodeType.GENERATE_STATISTICS)
builder.add_edge(CrossTopicGraphNodeType.GENERATE_STATISTICS, CrossTopicGraphNodeType.ANALYZE_PAPERS)
builder.add_edge(CrossTopicGraphNodeType.ANALYZE_PAPERS, CrossTopicGraphNodeType.GENERATE_SUMMARY)
builder.add_edge(CrossTopicGraphNodeType.GENERATE_SUMMARY, CrossTopicGraphNodeType.SAVE_FILES)
builder.add_edge(CrossTopicGraphNodeType.SAVE_FILES, END)

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)

