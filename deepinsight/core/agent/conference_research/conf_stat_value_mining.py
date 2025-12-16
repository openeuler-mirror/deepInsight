import logging
import os
from enum import Enum
from typing import Annotated, List

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_experimental.tools import PythonREPLTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import END
from langgraph.graph import add_messages, StateGraph
from langgraph.types import Command

from deepinsight.core.tools.mem_file_system import mem_file_system_instance
from deepinsight.core.tools.wordcloud_tool import generate_wordcloud
from deepinsight.core.utils.progress_utils import progress_stage
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.tools.tavily_search import tavily_search
from deepinsight.utils.db_schema_utils import get_db_models_source_markdown

from integrations.mcps.generate_chart import generate_column_chart, generate_bar_chart, generate_pie_chart


def normalize_messages(msgs):
    normalized = []
    for m in msgs:
        role = "assistant" if isinstance(m, AIMessage) else "user"
        content = m.content

        # 如果 content 不是字符串，统一转成字符串
        if not isinstance(content, str):
            content = str(content)

        normalized.append({"role": role, "content": content})
    return normalized


async def get_deep_agents(config: RunnableConfig, prompt_template_name, extent_tools=None, prompt_vars: dict = None):
    rc = parse_research_config(config)
    llm_model = rc.get_model()
    prompt_manager = rc.prompt_manager
    prompt_group: str = rc.prompt_group
    vars_to_format = dict(prompt_vars or {})
    vars_to_format.setdefault("db_models_description", get_db_models_source_markdown())
    system_prompt = prompt_manager.get_prompt(
        name=prompt_template_name,
        group=prompt_group,
    ).format(**vars_to_format)
    tools = [PythonREPLTool(), tavily_search, generate_wordcloud, generate_column_chart, generate_bar_chart,
             generate_pie_chart]
    if extent_tools:
        tools.extend(extent_tools)

    agent = create_deep_agent(
        model=llm_model,
        tools=tools,
        system_prompt=system_prompt,
        backend=mem_file_system_instance,
        middleware=[ModelFallbackMiddleware(llm_model, llm_model)]
    )
    return agent


class ConferenceStaticNodeType(str, Enum):
    TECH_TOPICS = "tech_topics"
    RESEARCH_HOTSPOTS = "research_hotspots"
    NATIONAL_TECH_PROFILE = "national_tech_profile"
    INSTITUTION_OVERVIEW = "institution_overview"
    INTER_INSTITUTION_COLLAB = "inter_institution_collab"
    HIGH_POTENTIAL_TECH_TRANSFER = "high_potential_tech_transfer"  # 修正拼写
    ACADEMIC_LEADERS = "academic_leaders"
    STATIC_SUMMARY = "static_summary"
    START = "start"


class ConferenceStaticState(dict):
    messages: Annotated[List[BaseMessage], add_messages]
    origin_question: str  # 从历史记录提取相关会议，格式为：年份 + 学术会议名称
    tech_topics: str  # 技术主题分析
    research_hotspots: str  # 研究热点识别与跨领域技术融合趋势分析
    national_tech_profile: str  # 国家技术特征与创新能力分析
    institution_overview: str  # 机构技术投入、科研产出及综合实力分析
    inter_institution_collab: str  # 跨机构合作网络特征与协作强度分析
    high_potential_tech_transfer: str  # 高潜力技术转化机会与产业化前景分析
    academic_leaders: str  # 学术带头人及核心研究者的影响力与研究方向识别
    static_summary: str


async def start_node(state: ConferenceStaticState, config: RunnableConfig):
    # 可以在这里添加一些初始化逻辑
    logging.info("Starting the conference static analysis process...")
    prompt = ('从用户输入的命令中提取出用户想分析的学术会议名称和年份，注意仅输出学术会议名称和年份，不要输出其它任何内容，格式为：年份 + 学术会议名称。示例(仅做参考)：2025年ICHEP学术会议，'
              '注意：历史对话中可能包含干扰信息，你仅关注提取学术会议相关信息')
    logging.info(f"state messages: {state['messages']}")
    # ✅ 在这里修正 messages
    history_messages = normalize_messages(state["messages"])
    messages = [{"role": "system", "content": prompt}] + history_messages
    rc = parse_research_config(config)
    llm = rc.get_model()
    response = await llm.ainvoke(messages)
    logging.info(f"origin_question: {response.content}")
    return Command(
        goto=[ConferenceStaticNodeType.NATIONAL_TECH_PROFILE, ConferenceStaticNodeType.INSTITUTION_OVERVIEW,
              ConferenceStaticNodeType.TECH_TOPICS, ConferenceStaticNodeType.RESEARCH_HOTSPOTS,
              ConferenceStaticNodeType.INTER_INSTITUTION_COLLAB, ConferenceStaticNodeType.HIGH_POTENTIAL_TECH_TRANSFER],
        update={"origin_question": response.content}
    )


@progress_stage("技术主题分析")
async def tech_topics_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/tech_topics.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="tech_topics_prompt",
                                           prompt_vars={"output_file": output_file})
    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""
        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get tech_topics failed, origin question:{state['origin_question']}")
        return {"tech_topics": ""}
    return {"tech_topics": mem_file_system_instance.read(output_file)}


@progress_stage("研究热点分析")
async def research_hotspots_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/research_hotspots.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="research_hotspots_prompt",
                                           extent_tools=[generate_wordcloud], prompt_vars={"output_file": output_file})
    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""
        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get research_hotspots failed, origin question:{state['origin_question']}")
        return {"research_hotspots": ""}
    return {
        "research_hotspots": mem_file_system_instance.read(output_file)
    }


@progress_stage("国家/地区技术特征分析")
async def national_tech_profile_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/national_tech_profile.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="national_tech_profile_prompt",
                                           prompt_vars={"output_file": output_file})

    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""
        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get national_tech_profile failed, origin question:{state['origin_question']}")
        return {"national_tech_profile": ""}
    return {
        "national_tech_profile": mem_file_system_instance.read(output_file)
    }


@progress_stage("机构技术特征分析")
async def institution_overview_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/institution_overview.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="institution_overview_prompt",
                                           prompt_vars={"output_file": output_file})

    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""

        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get institution_overview failed, origin question:{state['origin_question']}")
        return {"institution_overview": ""}
    return {
        "institution_overview": mem_file_system_instance.read(output_file)
    }


@progress_stage("跨机构合作网络分析")
async def inter_institution_collab_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/inter_institution_collab.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="inter_institution_collab_prompt",
                                           prompt_vars={"output_file": output_file})
    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""
        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get inter_institution_collab failed, origin question:{state['origin_question']}")
        return {"inter_institution_collab": ""}
    return {
        "inter_institution_collab": mem_file_system_instance.read(output_file)
    }


@progress_stage("高潜作者转化分析")
async def high_potential_tech_transfer_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/high_potential_tech_transfer.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="high_potential_tech_transfer_prompt",
                                           prompt_vars={"output_file": output_file})
    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""
        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get high_potential_tech failed, origin question:{state['origin_question']}")
        return {"high_potential_tech_transfer": ""}
    return {
        "high_potential_tech_transfer": mem_file_system_instance.read(output_file)
    }

@progress_stage("学术带头人分析")
async def academic_leaders_node(state: ConferenceStaticState, config: RunnableConfig):
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/academic_leaders.md"
    agent_instance = await get_deep_agents(config=config, prompt_template_name="academic_leaders_prompt",
                                           prompt_vars={"output_file": output_file})
    input_messages = [
        {
            "role": "user",
            "content": f"""请分析学术会议：{state['origin_question']}，最终结果输出到文件：{output_file}"""
        }
    ]
    # Try-Catch to handle errors and print detailed stack trace
    try:
        await agent_instance.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logging.error(f"Error during ainvoke call: {e}")
        logging.exception("Exception details:")
        # Optionally, log the stack trace to provide more details
        raise  # Reraise the exception to handle it further up the call chain if necessary
    if not mem_file_system_instance.exists(output_file):
        logging.error(f"get academic_leaders failed, origin question:{state['origin_question']}")
        return {"high_potential_tech_transfer": ""}
    return {
        "high_potential_tech_transfer": mem_file_system_instance.read(output_file)
    }


async def static_summary_node(state: ConferenceStaticState, config: RunnableConfig):
    logging.info(f" begin write file from mem to disk")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 构造相对项目根目录的路径
    # 假设当前脚本在项目根目录或子目录下
    rc = parse_research_config(config)
    work_root = getattr(rc, "work_root", None)
    if not work_root:
        work_root = os.getcwd()
    output_path = os.path.join(work_root, "conference_report_result", rc.thread_id, "conference_value_mining")
    output_dir = f"/{str(rc.run_id)}/"
    logging.info(f"static_summary_node output_path:{output_path}, output_path:{output_dir}")
    mem_file_system_instance.sync_with_real_fs(real_dir=output_path, folder_path=output_dir)

    summary_parts = []
    if state.get('tech_topics'):
        summary_parts.append(state['tech_topics'])
    if state.get('research_hotspots'):
        summary_parts.append(state['research_hotspots'])
    if state.get('national_tech_profile'):
        summary_parts.append(state['national_tech_profile'])
    if state.get('institution_overview'):
        summary_parts.append(state['institution_overview'])
    if state.get('inter_institution_collab'):
        summary_parts.append(state['inter_institution_collab'])
    if state.get('high_potential_tech_transfer'):
        summary_parts.append(state['high_potential_tech_transfer'])
    find_result = '\n\n\n'.join(summary_parts)
    logging.info(f"static_summary: {find_result}")
    return {"static_summary": '\n\n\n'.join(summary_parts)}


conf_stat_graph_builder = StateGraph(ConferenceStaticState)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.START, start_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.NATIONAL_TECH_PROFILE, national_tech_profile_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.TECH_TOPICS, tech_topics_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.RESEARCH_HOTSPOTS, research_hotspots_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.INSTITUTION_OVERVIEW, institution_overview_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.INTER_INSTITUTION_COLLAB, inter_institution_collab_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.HIGH_POTENTIAL_TECH_TRANSFER,
                                 high_potential_tech_transfer_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.ACADEMIC_LEADERS, academic_leaders_node)
# builder.add_node(ConferenceStaticNodeType.START, start_node)
conf_stat_graph_builder.add_node(ConferenceStaticNodeType.STATIC_SUMMARY, static_summary_node)

# 入口节点
conf_stat_graph_builder.set_entry_point(ConferenceStaticNodeType.START)

conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.NATIONAL_TECH_PROFILE,
                                 ConferenceStaticNodeType.STATIC_SUMMARY)
conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.INSTITUTION_OVERVIEW, ConferenceStaticNodeType.STATIC_SUMMARY)
conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.TECH_TOPICS, ConferenceStaticNodeType.STATIC_SUMMARY)
conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.RESEARCH_HOTSPOTS, ConferenceStaticNodeType.STATIC_SUMMARY)
conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.INTER_INSTITUTION_COLLAB,
                                 ConferenceStaticNodeType.STATIC_SUMMARY)
conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.HIGH_POTENTIAL_TECH_TRANSFER,
                                 ConferenceStaticNodeType.STATIC_SUMMARY)
conf_stat_graph_builder.add_edge(ConferenceStaticNodeType.STATIC_SUMMARY, END)
checkpointer = InMemorySaver()
conf_stat_graph = conf_stat_graph_builder.compile(checkpointer=checkpointer)
