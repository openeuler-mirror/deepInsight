import logging
import os
from enum import Enum
from typing import Annotated, List, Optional
from pydantic import BaseModel, Field

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_tavily import TavilySearch
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.constants import END
from langgraph.graph import StateGraph, add_messages
from langgraph.types import Command, interrupt

from deepinsight.core.tools.best_paper_analysis import batch_analyze_papers
from deepinsight.core.tools.paper_statistic import (
    affiliation_analysis,
    country_analysis,
    co_authorship_analysis,
    domain_analysis,
    first_author_analysis,
    authors_paper_analysis,
)
from deepinsight.core.utils.mcp_utils import MCPClientUtils
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.graph_config import ResearchConfig
from deepinsight.core.types.research import FinalResult

from deepinsight.core.agent.deep_research.supervisor import graph as deep_research_graph
from deepinsight.core.agent.conference_research.conf_stat_value_mining import conf_stat_graph
from deepinsight.core.tools.file_system import register_fs_tools, fs_instance


class ConferenceGraphNodeType(str, Enum):
    QUESTION_CLARIFY = "question_clarify"
    WAIT_QUESTION_CLARIFY = "wait_question_clarify"
    INSIGHT_SUMMARY = "summary"

    CONFERENCE_OVERVIEW = "conference_overview"
    CONFERENCE_SUBMISSION = "conference_submission"
    CONFERENCE_KEYNOTE = "conference_keynotes"
    CONFERENCE_TOPIC = "conference_topic"
    CONFERENCE_BEST_PAPER = "conference_best_papers"

    def __str__(self):
        return self.value


CONFERENCE_MEMBER_DESCRIPTION = {
    ConferenceGraphNodeType.CONFERENCE_OVERVIEW: "负责提供会议的整体概述，包括会议的主题、规模和重要性。",
    ConferenceGraphNodeType.CONFERENCE_SUBMISSION: "负责分析会议的投稿情况，包括投稿数量、录取率和主要研究方向。",
    ConferenceGraphNodeType.CONFERENCE_KEYNOTE: "负责总结会议的主旨演讲内容，突出重要的研究成果和趋势。",
    ConferenceGraphNodeType.CONFERENCE_TOPIC: "负责分析会议的各个分会场主题，识别热门研究领域和新兴趋势。",
    ConferenceGraphNodeType.CONFERENCE_BEST_PAPER: "负责评选和总结会议的最佳论文，强调其创新点和影响力。",
}


class QuestionClarify(BaseModel):
    """Model for user clarification requests."""

    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question.",
    )
    question: Optional[str] = Field(
        description="A question to ask the user to clarify the report scope",
    )
    particapant_members: Optional[List[str]] = Field(
        description="List of conference member roles that will participate in the research.",
    )


class ConferenceState(dict):
    messages: Annotated[List[BaseMessage], add_messages]
    conference_overview: str
    conference_submission: str
    conference_keynotes: str
    conference_topic: str
    conference_best_papers_summary: str
    conference_summary: str
    conference_best_papers: str

    def __init__(self, **kwargs):
        # 设置默认值
        defaults = {
            "messages": [],
            "conference_overview": "",
            "conference_submission": "",
            "conference_keynotes": "",
            "conference_topic": "",
            "conference_best_papers_summary": "",
            "conference_summary": "",
            "conference_best_papers": "",
        }
        # 合并用户传入的参数
        super().__init__({**defaults, **kwargs})


async def question_clarify_node(state: ConferenceState, config: RunnableConfig):
    rc: ResearchConfig = parse_research_config(config)
    prompt_template = rc.prompt_manager.get_prompt(
        name=ConferenceGraphNodeType.QUESTION_CLARIFY,
        group=rc.prompt_group,
    )
    members_desc_str = "\n".join(
        [f"- **{k.value}**: {v.strip()}" for k, v in CONFERENCE_MEMBER_DESCRIPTION.items()]
    )
    # 渲染原有系统提示词文本
    prompt = prompt_template.format(
        members=members_desc_str,
    )

    messages = [
                   SystemMessage(content=prompt),
               ] + state["messages"]

    llm = rc.get_model()
    parser = PydanticOutputParser(pydantic_object=QuestionClarify)
    prompt = prompt + "\n\n---\n" + parser.get_format_instructions()
    chain =  llm | parser

    result: QuestionClarify = await chain.with_retry().ainvoke(
        input=messages,
    )

    # 路由逻辑保持不变
    if result.need_clarification:
        return Command(
            goto=ConferenceGraphNodeType.WAIT_QUESTION_CLARIFY,
            update={"messages": [AIMessage(content=result.question)]}
        )
    else:
        return Command(
            goto=result.particapant_members,
        )


async def wait_user_clarify_node(state: ConferenceState):
    user_reply = interrupt(QuestionClarify(question=state["messages"][-1].content))
    return {
        "messages": [HumanMessage(content=user_reply)]
    }


async def construct_sub_config(config, prompt_group: ConferenceGraphNodeType):
    parent_configurable = config.get("configurable", {})
    tools = []
    if parent_configurable.get("tools"):
        tools.extend(parent_configurable["tools"])
    if prompt_group == ConferenceGraphNodeType.CONFERENCE_BEST_PAPER:
        tools.append(batch_analyze_papers)
    elif prompt_group == ConferenceGraphNodeType.CONFERENCE_SUBMISSION:
        tools.extend(
            [
                affiliation_analysis,
                country_analysis,
                co_authorship_analysis,
                domain_analysis,
                first_author_analysis,
                authors_paper_analysis,
            ]
        )
        chart_tools = await MCPClientUtils.get_tools(
            tools_name_list=["generate_bar_chart"],
            server_name="mcp-chart",
        )
        tools.extend(chart_tools)
    return {
        **config.get("configurable", {}),
        "prompt_group": prompt_group,
        "allow_user_clarification": False,
        "allow_edit_research_brief": False,
        "allow_edit_report_outline": False,
        "allow_publish_result": False,
        "tools": tools,
    }


async def conference_overview_node(state: ConferenceState, config: RunnableConfig):
    result = await deep_research_graph.with_config(
        configurable=await construct_sub_config(config, ConferenceGraphNodeType.CONFERENCE_OVERVIEW)
    ).ainvoke({
        "messages": state["messages"]
    })
    return {
        "conference_overview": result["final_report"]
    }


async def conference_submission_node(state: ConferenceState, config: RunnableConfig):
    result = await conf_stat_graph.with_config(
        configurable=await construct_sub_config(config, ConferenceGraphNodeType.CONFERENCE_SUBMISSION)
    ).ainvoke({
        "messages": state["messages"]
    })
    return {
        "conference_submission": result["static_summary"]
    }


async def conference_keynotes_node(state: ConferenceState, config: RunnableConfig):
    result = await deep_research_graph.with_config(
        configurable=await construct_sub_config(config, ConferenceGraphNodeType.CONFERENCE_KEYNOTE)
    ).ainvoke({
        "messages": state["messages"]
    })
    return {
        "conference_keynotes": result["final_report"]
    }


async def conference_topic_node(state: ConferenceState, config: RunnableConfig):
    result = await deep_research_graph.with_config(
        configurable=await construct_sub_config(config, ConferenceGraphNodeType.CONFERENCE_TOPIC)
    ).ainvoke({
        "messages": state["messages"]
    })
    return {
        "conference_topic": result["final_report"]
    }


async def conference_best_paper_node(state: ConferenceState, config: RunnableConfig):
    result = await deep_research_graph.with_config(
        configurable=await construct_sub_config(config, ConferenceGraphNodeType.CONFERENCE_BEST_PAPER)
    ).ainvoke({
        "messages": state["messages"]
    })
    rc = parse_research_config(config)
    output_file = f"/{str(rc.run_id)}/conference_best_papers"
    paper_files_content_map = fs_instance.read_all_files_in_dir(output_file)
    paper_file_content = "\n".join(content for _, content in paper_files_content_map.items())
    return {
        "conference_best_papers_summary": result["final_report"],
        "conference_best_papers": paper_file_content,
    }


async def insight_summary_node(state: ConferenceState, config: RunnableConfig):
    rc = parse_research_config(config)
    model = rc.get_model()
    summary_prompt = rc.prompt_manager.get_prompt(
        name=ConferenceGraphNodeType.INSIGHT_SUMMARY,
        group=rc.prompt_group,
    ).format()
    output_file = f"/{str(rc.run_id)}/conference_summary.md"
    logging.debug(
        f"conference_best_papers_summary:{state['conference_best_papers_summary']}, conference_topic:{state.get('conference_topic', '')}")
    user_prompt = f"学术会议价值论文列表：{state['conference_best_papers_summary']},会议主题相关信息：{state.get('conference_topic', '')},保存到路径：{output_file} "
    tools = register_fs_tools(fs_instance)
    tool_instance = TavilySearch(
        max_results=2,
        topic="general",
        include_answer=True,
        include_raw_content=False,
        include_images=False,
        include_image_descriptions=True,
        search_depth="advanced",
    )
    tools.append(tool_instance)

    logging.debug(f"begin execute summary deep agent")
    # Create the deep agent
    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=summary_prompt,
    )

    try:
        agent.invoke({"messages": [
            {
                "role": "user",
                "content": user_prompt
            }]}, config=config)
    except Exception as e:
        logging.error(f"An error occurred:{e}")
        import traceback
        traceback.print_exc()  # 打印堆栈信息

    # 2. 把内存文件写入到本地存储中
    # 获取当前脚本所在的绝对路径
    logging.debug(f" begin write file from mem to disk")
    thread_id = rc.thread_id
    # 优先使用配置中的工作路径；若不存在则回退到当前工作目录
    work_root = getattr(rc, "work_root", None)
    if not work_root:
        work_root = os.getcwd()
    output_path = os.path.join(work_root, "conference_report_result", thread_id)
    output_dir = f"/{str(rc.run_id)}/"
    fs_instance.export_to_real_fs(real_dir=output_path, folder_path=output_dir)
    state['conference_summary'] = fs_instance.read_file(f"{output_dir}/conference_summary.md")

    full_text = (
            state['conference_overview'] + '\n\n\n' +
            state['conference_submission'] + '\n\n\n' +
            state['conference_keynotes'] + '\n\n\n' +
            state['conference_topic'] + '\n\n\n' +
            state['conference_best_papers'] + '\n\n\n' +
            state['conference_summary']
    )
    # 3. 把输出吐到前端；
    writer = get_stream_writer()
    writer(FinalResult(
        final_report=full_text,
    ))


builder = StateGraph(ConferenceState)

# 注册节点
builder.add_node(ConferenceGraphNodeType.QUESTION_CLARIFY, question_clarify_node)
builder.add_node(ConferenceGraphNodeType.WAIT_QUESTION_CLARIFY, wait_user_clarify_node)
builder.add_node(ConferenceGraphNodeType.CONFERENCE_OVERVIEW, conference_overview_node)
builder.add_node(ConferenceGraphNodeType.CONFERENCE_SUBMISSION, conference_submission_node)
builder.add_node(ConferenceGraphNodeType.CONFERENCE_KEYNOTE, conference_keynotes_node)
builder.add_node(ConferenceGraphNodeType.CONFERENCE_TOPIC, conference_topic_node)
builder.add_node(ConferenceGraphNodeType.CONFERENCE_BEST_PAPER, conference_best_paper_node)
builder.add_node(ConferenceGraphNodeType.INSIGHT_SUMMARY, insight_summary_node)

# 入口节点
builder.set_entry_point(ConferenceGraphNodeType.QUESTION_CLARIFY)
builder.add_edge(ConferenceGraphNodeType.WAIT_QUESTION_CLARIFY, ConferenceGraphNodeType.QUESTION_CLARIFY)
builder.add_edge(ConferenceGraphNodeType.CONFERENCE_OVERVIEW, ConferenceGraphNodeType.INSIGHT_SUMMARY)
builder.add_edge(ConferenceGraphNodeType.CONFERENCE_SUBMISSION, ConferenceGraphNodeType.INSIGHT_SUMMARY)
builder.add_edge(ConferenceGraphNodeType.CONFERENCE_KEYNOTE, ConferenceGraphNodeType.INSIGHT_SUMMARY)
builder.add_edge(ConferenceGraphNodeType.CONFERENCE_TOPIC, ConferenceGraphNodeType.INSIGHT_SUMMARY)
builder.add_edge(ConferenceGraphNodeType.CONFERENCE_BEST_PAPER, ConferenceGraphNodeType.INSIGHT_SUMMARY)

builder.add_edge(ConferenceGraphNodeType.INSIGHT_SUMMARY, END)

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)
