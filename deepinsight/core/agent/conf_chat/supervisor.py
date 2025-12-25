import logging
import os
from enum import Enum
from typing import Any, TypedDict, Literal, Annotated, List

from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.constants import END
from langgraph.graph import StateGraph, add_messages
from langgraph.types import Command, interrupt
from langmem.short_term import SummarizationNode

from deepinsight.core.utils.tool_utils import create_retrieval_tool
from deepinsight.core.utils.progress_utils import progress_stage
from deepinsight.utils.tavily_key_utils import select_api_key
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.research import FinalResult
from deepinsight.core.types.graph_config import RetrievalType
from deepinsight.core.agent.conf_gen.supervisor import graph as conference_research_graph
from deepinsight.core.agent.conf_chat.statistics import graph as statistics_graph
from deepinsight.core.tools.tavily_search import tavily_search
from deepinsight.core.tools.wordcloud_tool import generate_wordcloud
from deepinsight.service.schemas.research import SceneType
from integrations.mcps.generate_chart import generate_area_chart, generate_bar_chart, generate_column_chart, \
    generate_pie_chart, generate_scatter_chart, generate_line_chart, generate_radar_chart


class GraphNodeType(str, Enum):
    SUMMARIZER = "summarizer"  # 对话摘要节点
    SUPERVISOR = "supervisor"  # 监督者节点（任务分配）
    ANSWER_COMPOSER = "answer_composer"  # 答案汇总节点
    CLARIFY_NODE = "question_clarify"  # 问题澄清节点
    PAPER_TEAM = "paper_team"  # 论文团队节点
    RETRIVAL_TEAM = "retrival_team"  # 检索团队节点
    DEEP_RESEARCH_TEAM = "deep_research_team"  # 深度研究团队节点
    CHART_NODE = "chart_node"  # 报告（图表）团队节点


# 定义状态格式
class SupervisorState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    context: dict[str, Any]


async def summarization_node(state: SupervisorState, config):
    rc = parse_research_config(config)
    summarizer = SummarizationNode(
        model=rc.get_model(),
        max_tokens=32768,
        max_tokens_before_summary=2048,
        max_summary_tokens=16384,
        output_messages_key="messages"
    )
    result = await summarizer.ainvoke(state)
    return result


@progress_stage("生成回复")
async def answer_composer_node(state: SupervisorState, config):
    rc = parse_research_config(config)
    prompt_template = rc.prompt_manager.get_prompt(
        name="answer_composer_prompt",
        group=rc.prompt_group,
    )
    system_prompt = prompt_template.format(
        messages=state["messages"]
    )

    response = await rc.get_model().ainvoke([
        {"role": "system", "content": system_prompt}
    ])

    writer = get_stream_writer()
    writer(FinalResult(
        final_report=response.content
    ))


def make_supervisor_node():
    def parse_response(response_content: str):
        import json
        """解析模型返回的内容，提取next_step"""
        try:
            # 尝试直接解析JSON
            logging.debug(response_content)
            return json.loads(response_content)
        except Exception:
            # 如果直接解析失败，尝试提取JSON部分
            start = response_content.find('{')
            end = response_content.rfind('}') + 1
            if start != -1 and end != -1:
                json_str = response_content[start:end]
                return json.loads(json_str)
        return None

    async def supervisor_node(state: SupervisorState, config) -> Command[Literal[
        GraphNodeType.CLARIFY_NODE, GraphNodeType.PAPER_TEAM, GraphNodeType.CHART_NODE,
        GraphNodeType.ANSWER_COMPOSER]]:
        rc = parse_research_config(config)
        # 1. 定义新成员和描述
        members = [
            GraphNodeType.CLARIFY_NODE.value,
            GraphNodeType.PAPER_TEAM.value,
            GraphNodeType.CHART_NODE.value,
            GraphNodeType.RETRIVAL_TEAM.value,
            GraphNodeType.DEEP_RESEARCH_TEAM.value
        ]

        members_description = {
            GraphNodeType.CLARIFY_NODE: rc.prompt_manager.get_prompt(
                name="clarify_node_prompt",
                group=rc.prompt_group,
            ).format(),
            GraphNodeType.PAPER_TEAM: rc.prompt_manager.get_prompt(
                name="paper_team_prompt",
                group=rc.prompt_group,
            ).format(),
            GraphNodeType.RETRIVAL_TEAM: rc.prompt_manager.get_prompt(
                name="retrieval_team_prompt",
                group=rc.prompt_group,
            ).format(),
            GraphNodeType.CHART_NODE: rc.prompt_manager.get_prompt(
                name="report_team_prompt",
                group=rc.prompt_group,
            ).format(),
            GraphNodeType.DEEP_RESEARCH_TEAM: rc.prompt_manager.get_prompt(
                name="deep_research_team_prompt",
                group=rc.prompt_group,
            ).format(),
        }

        members_str = "\n".join([f"- {m}" for m in members])
        member_list_string = ', '.join([f'"{node}"' for node in members])
        members_desc_str = "\n".join(
            [f"- **{k.value}**: {v.strip()}" for k, v in members_description.items()]
        )

        prompt_template = rc.prompt_manager.get_prompt(
            name="supervisor_prompt",
            group=rc.prompt_group,
        )
        conf_analysis_supervisor_prompt = prompt_template.format(
            members=members_str,
            members_description=members_desc_str,
            member_list=member_list_string
        )

        messages = [
                       {"role": "system", "content": conf_analysis_supervisor_prompt},
                   ] + state["messages"]
        llm = rc.get_model()
        response = await llm.ainvoke(messages)
        llm_response = response.content
        result = parse_response(llm_response)
        if not result or result["next"] == END or result["next"] == GraphNodeType.CLARIFY_NODE.value:
            return Command(
                goto=GraphNodeType.ANSWER_COMPOSER.value,
                update={"messages": {"role": "ai", "content": llm_response}}
            )

        return Command(
            goto=result["next"]
        )

    return supervisor_node


async def question_clarify_node(state: SupervisorState) -> Command[Literal[GraphNodeType.SUMMARIZER]]:
    user_reply = interrupt(state["messages"][-1].content)
    return Command(goto=GraphNodeType.SUMMARIZER.value, update={
        "messages": HumanMessage(
            content=user_reply
        )
    })


@progress_stage("论文统计分析")
async def paper_team_node(state: SupervisorState) -> Command[Literal[GraphNodeType.SUMMARIZER]]:
    # 调用 Paper Team 的处理流程
    result = await statistics_graph.ainvoke(
        {"messages": [("user", state["messages"][-1].content)]},
        {"recursion_limit": 100},
    )
    return Command(goto=GraphNodeType.SUMMARIZER.value, update={
        "messages": HumanMessage(
            content=result["messages"][-1].content, name="paper_team"
        )
    })


@progress_stage("论文检索")
async def retrival_team_node(state: SupervisorState, config: RunnableConfig) -> Command[Literal[END]]:

    rc = parse_research_config(config)
    tools = [tavily_search]
    
    # Add all configured retrieval tools
    if rc.retrieval_config:
        for retrieval_type in rc.retrieval_config.keys():
            try:
                retrieval_tool = create_retrieval_tool(retrieval_type, config)
                tools.append(retrieval_tool)
            except Exception as e:
                logging.warning(f"Failed to create retrieval tool for {retrieval_type}: {e}")
    # 调用 retrival Team 的处理流程
    system_prompt = """ 
    你是一名专精于学术论文检索与数据分析的智能研究助理。  
    你的任务是根据用户请求，**高效查询学术论文、会议论文及相关作者/机构信息**。  
    你应当优先利用 RAG 检索（ragflow）进行信息查找；  
    若 RAG 不可用或返回结果为空，则自动使用 Tavily 搜索工具进行查询。

    ---

    ### 🎯 工作目标
    1. **优先使用 RAG 检索知识库（ragflow）获取论文、作者、会议信息。**
    2. **当 RAG 检索无结果或无法使用时，自动切换至 Tavily 搜索。**
    3. **每次查询完成后，主动反思结果是否满足用户问题。**
       - 如果结果不完整或不相关，请优化查询语句（query）并重试。
    4. **最多尝试 5 次查询。**
       - 若超过 5 次仍未找到有效结果，则返回检索失败的提示（例如：“未能检索到相关论文或信息”）。

    ---

    ### 🧰 可用工具
    - **ragflow.knowledge_retrieve**：RAG 检索学术知识库内容。
    - **tavily_search**：从互联网检索学术论文、会议及作者信息。

    ---

    ### 🧠 检索与反思流程

    每次检索请执行以下逻辑：

    1. **执行查询**
       * 优先使用 ragflow 进行知识检索；
       * 若 RAG 无法使用或结果为空，则改用 tavily_search。

    2. **结果评估**
       * 检查结果是否满足用户问题；
       * 如果不满足，重写查询语句并重试。

    3. **重试机制**
       * 最多执行 5 次；
       * 超过 5 次仍无结果则返回失败信息。

    ---

    ### 📘 输出要求

    * 所有内容必须来源于检索结果，不得编造、估算或推断。
    * 回答要简洁、准确，并与用户问题直接相关。
    * 若涉及数据库查询，请仅使用 PythonREPLTool 和 SQLAlchemy。
    * 不进行统计、趋势分析或额外评论，除非用户明确要求。

    ---

    ### 📄 输出格式规范（新增）

    每次输出结果时，请严格按照以下格式组织内容：

    #### ✅ 标准输出格式

    ```
    <在此展示检索得到的论文摘要、会议介绍或作者信息原文，不做改写>

    【来源】

    * 来源类型：RAG 检索 / 网络检索（Tavily）
    * 来源名称：<数据库名或网站名，如 “IEEE Xplore”, “ACM Digital Library”, “Google Scholar”, “arXiv”, “SpringerLink” 等>
    * 检索时间：<自动填入检索执行的时间，如 2025-11-12 14:35>
    * 原始链接（若有）：<论文或数据源的具体链接>

    ```

    #### ✅ 多条结果输出格式

    若返回多篇论文或多个来源，请以编号形式列出：
    ```

    <论文摘要或核心内容>

    【来源】

    * 来源类型：RAG 检索
    * 来源名称：ACM Digital Library
    * 检索时间：2025-11-12 14:35
    * 原始链接：[https://dl.acm.org/](https://dl.acm.org/)...


    * 来源类型：网络检索（Tavily）
    * 来源名称：Google Scholar
    * 检索时间：2025-11-12 14:36
    * 原始链接：[https://scholar.google.com/](https://scholar.google.com/)...

    ```

    ---

    ### 🧩 附加说明
    * 若检索失败，请输出：
    ```

    ❌ 未能检索到相关论文或信息，请尝试更换关键词或调整查询范围。

    ```
    * 若部分结果存在信息缺失，请明确标注“[信息缺失]”。

    """
    agent = create_agent(
        model=rc.get_model(),
        tools=tools,  # Many tools
        middleware=[TodoListMiddleware()],
        system_prompt=system_prompt
    )
    result = await agent.ainvoke(state, config=config)
    return Command(
        goto=GraphNodeType.SUMMARIZER.value,
        update={
            "messages": [
                HumanMessage(content=result["messages"][-1].content, name="retrival_team")
            ]
        }
    )


@progress_stage("图表生成")
async def chart_node(state: SupervisorState, config: RunnableConfig) -> Command[Literal[GraphNodeType.SUMMARIZER]]:
    rc = parse_research_config(config)
    llm = rc.get_model()

    chart_tools = [generate_area_chart, generate_bar_chart, generate_column_chart, generate_pie_chart,
                   generate_scatter_chart, generate_line_chart, generate_radar_chart, generate_wordcloud]
    system_prompt = rc.prompt_manager.get_prompt(
        name="report_chart_agent_sys_prompt",
        group=rc.prompt_group,
    ).format()
    agent = create_agent(
        model=llm,
        tools=chart_tools,
        system_prompt=system_prompt
    )

    result = await agent.ainvoke(
        {
            "user_input": state["messages"][-1].content,
            "messages": [
                {"role": "human", "content": state["messages"][-1].content}
            ],
            "charts": [],
            "report": "",
        })

    return Command(goto=GraphNodeType.SUMMARIZER.value, update={
        "messages": HumanMessage(
            content=result["messages"][-1].content, name="report_team"
        )
    })


@progress_stage("顶会深度研究")
async def deep_research_team_node(state: SupervisorState, config: RunnableConfig) -> Command[Literal[END]]:
    # 调用 retrival Team 的处理流程
    if os.getenv("TAVILY_API_KEYS"):
        selected_key, all_keys_usage = select_api_key()
        if selected_key is None:
            logging.error("no tavily key can be used, please set first.")
            for key, usage in all_keys_usage.items():
                logging.error(f"API Key: {key} - Plan Limit: {usage['plan_limit']}, Plan Usage: {usage['plan_usage']}")
            writer = get_stream_writer()
            writer(FinalResult(
                final_report="no tavily key can be used, please set first."
            ))
            return Command(goto=END)

    parent_configurable = config.get("configurable", {})
    deep_research_config = {
        **parent_configurable,
        "prompt_group": "conf_gen_supervisor",
        "allow_user_clarification": False,
        "allow_edit_research_brief": False,
        "allow_edit_report_outline": False,
        "allow_publish_result": False,
    }

    result = await conference_research_graph.with_config(configurable=deep_research_config).ainvoke(
        {"messages": [("user", state["messages"][-1].content)]}
    )
    writer = get_stream_writer()
    writer({"result": result["messages"][-1].content})
    return Command(goto=END, update={
        "messages": HumanMessage(
            content=result["messages"][-1].content, name="deep_research_team"
        )
    })


# 构建图
builder = StateGraph(SupervisorState)
builder.add_node(GraphNodeType.SUMMARIZER.value, summarization_node)
builder.add_node(GraphNodeType.SUPERVISOR.value, make_supervisor_node())
builder.add_node(GraphNodeType.CLARIFY_NODE.value, question_clarify_node)
builder.add_node(GraphNodeType.PAPER_TEAM.value, paper_team_node)
builder.add_node(GraphNodeType.CHART_NODE.value, chart_node)
builder.add_node(GraphNodeType.RETRIVAL_TEAM.value, retrival_team_node)
builder.add_node(GraphNodeType.DEEP_RESEARCH_TEAM.value, deep_research_team_node)
builder.add_node(GraphNodeType.ANSWER_COMPOSER.value, answer_composer_node)


builder.set_entry_point(GraphNodeType.SUMMARIZER.value)
builder.add_edge(GraphNodeType.SUMMARIZER.value, GraphNodeType.SUPERVISOR.value)
builder.add_edge(GraphNodeType.ANSWER_COMPOSER.value, END)
checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)
