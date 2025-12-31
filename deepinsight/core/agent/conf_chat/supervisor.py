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
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.research import FinalResult
from deepinsight.core.types.graph_config import RetrievalType
from deepinsight.core.agent.conf_gen.supervisor import graph as conference_research_graph
from deepinsight.core.agent.conf_gen.cross_topic_supervisor import (
    graph as cross_topic_graph,
    construct_sub_config as construct_cross_topic_sub_config,
)
from deepinsight.core.agent.conf_chat.statistics import graph as statistics_graph
from deepinsight.core.tools.tavily_search import tavily_search
from deepinsight.core.tools.wordcloud_tool import generate_wordcloud
from deepinsight.service.schemas.research import SceneType
from deepinsight.utils.tavily_managed import default_tavily_key_manager, TavilyNoEnvError, TavilyNoAvailableKeyError
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
    CROSS_TOPIC_TEAM = "cross_topic_team"  # 跨会议主题分析团队节点


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
        GraphNodeType.CLARIFY_NODE,
        GraphNodeType.PAPER_TEAM,
        GraphNodeType.CHART_NODE,
        GraphNodeType.RETRIVAL_TEAM,
        GraphNodeType.DEEP_RESEARCH_TEAM,
        GraphNodeType.CROSS_TOPIC_TEAM,
        GraphNodeType.ANSWER_COMPOSER,
    ]]:
        rc = parse_research_config(config)
        
        # 检查是否有多个知识库（对应多个会议）
        kb_count = 0
        conference_hint = ""
        if rc.retrieval_config:
            for retrieval_type, retrieval_config in rc.retrieval_config.items():
                if hasattr(retrieval_config, 'args') and hasattr(retrieval_config.args, 'kb_ids'):
                    kb_ids = retrieval_config.args.kb_ids or []
                    kb_count = len(kb_ids)
                    if kb_count > 1:
                        # 如果有多个知识库，提示 supervisor 考虑跨会议场景
                        conference_hint = (
                            f"\n\n【重要提示】当前请求涉及 {kb_count} 个会议的知识库。"
                            f"如果用户问题是关于技术主题的分析、对比或研究，"
                            f"应该考虑使用 cross_topic_team 进行跨会议主题分析。"
                        )
                        logging.info(f"检测到 {kb_count} 个知识库，提示 supervisor 考虑跨会议场景")
                        break
        
        # 1. 定义新成员和描述
        members = [
            GraphNodeType.CLARIFY_NODE.value,
            GraphNodeType.PAPER_TEAM.value,
            GraphNodeType.CHART_NODE.value,
            GraphNodeType.RETRIVAL_TEAM.value,
            GraphNodeType.DEEP_RESEARCH_TEAM.value,
            GraphNodeType.CROSS_TOPIC_TEAM.value,
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
            GraphNodeType.CROSS_TOPIC_TEAM: rc.prompt_manager.get_prompt(
                name="cross_topic_team_prompt",
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
        
        # 如果有多个会议，在 prompt 末尾添加提示
        if conference_hint:
            conf_analysis_supervisor_prompt += conference_hint

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
    try:
        default_tavily_key_manager().get_client()
    except (TavilyNoEnvError, TavilyNoAvailableKeyError):
        logging.error("no tavily key can be used, please set first.")
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


@progress_stage("跨会议主题分析")
async def cross_topic_team_node(state: SupervisorState, config: RunnableConfig) -> Command[Literal[END]]:
    """
    跨会议主题分析团队节点

    - 仍然在 CONFERENCE_QA 场景下工作，由 supervisor_prompt 决定是否派单到本节点
    - kb_ids 从 config.retrieval_config 中获取，与 deep_research_team_node 保持一致
    - 使用 conf_gen_cross_topic 的 prompt_group 和 cross_topic_supervisor 图完成跨会议分析，
      生成统计信息、论文分析和总结等 MD 文件。
    """
    rc = parse_research_config(config)
    question = state["messages"][-1].content

    writer = get_stream_writer()

    # 构建子图配置：切换到跨会议场景的 prompt_group 等配置
    # 复用 cross_topic_supervisor 中的 construct_sub_config 逻辑
    # 注意：retrieval_config 会从父 config 中继承，不需要显式传递
    sub_config = await construct_cross_topic_sub_config(config, prompt_group="conf_gen_cross_topic")

    # 初始化状态（与 deep_research_team_node 保持一致，不传递 kb_ids）
    initial_state = {
        "messages": [("user", question)],
        "question": question,
    }

    try:
        result = await cross_topic_graph.with_config(
            configurable=sub_config
        ).ainvoke(initial_state)

        # 从 result 中获取完整报告内容（类似 deep_research_team_node）
        # 优先从 messages 中获取，如果没有则从 final_report 获取
        if result.get("messages") and len(result["messages"]) > 0:
            full_report = result["messages"][-1].content
        else:
            # 如果没有 messages，尝试从输出目录读取完整报告
            output_path = result.get("output_path", "")
            full_report = None
            if output_path:
                # 尝试读取生成的 PDF 对应的 Markdown 文件
                # 或者从各个组件文件组装
                try:
                    import os
                    from deepinsight.core.types.conference_constants import (
                        ConferenceFileNames,
                        ConferenceFolderNames,
                    )
                    
                    statistics_path = os.path.join(output_path, ConferenceFileNames.CROSS_TOPIC_STATISTICS_MD)
                    summary_path = os.path.join(output_path, ConferenceFileNames.CROSS_TOPIC_SUMMARY_MD)
                    papers_dir = os.path.join(output_path, ConferenceFolderNames.CROSS_TOPIC_PAPERS)
                    papers_list_path = os.path.join(output_path, "papers_list.md")
                    
                    parts = []
                    if os.path.exists(statistics_path):
                        with open(statistics_path, 'r', encoding='utf-8') as f:
                            parts.append(f"# 统计信息\n\n{f.read()}")
                    
                    if os.path.exists(papers_dir):
                        paper_files = sorted([f for f in os.listdir(papers_dir) if f.endswith(".md")])
                        if paper_files:
                            parts.append("\n\n# 论文分析\n\n")
                            for paper_file in paper_files:
                                paper_path = os.path.join(papers_dir, paper_file)
                                with open(paper_path, 'r', encoding='utf-8') as f:
                                    parts.append(f.read())
                                    parts.append("\n\n---\n\n")
                    
                    if os.path.exists(summary_path):
                        with open(summary_path, 'r', encoding='utf-8') as f:
                            parts.append(f"# 总结\n\n{f.read()}")
                    
                    if os.path.exists(papers_list_path):
                        with open(papers_list_path, 'r', encoding='utf-8') as f:
                            parts.append(f"\n\n# 论文列表\n\n{f.read()}")
                    
                    if parts:
                        from datetime import datetime
                        now = datetime.now()
                        time_str = now.strftime("%Y年%m月%d日 %H时%M分%S秒")
                        header = (
                            f"# 跨会议主题分析报告\n\n"
                            f"**研究主题**：{question}\n\n"
                            f"**生成时间**：{time_str}\n\n"
                            f"---\n\n"
                        )
                        full_report = header + "\n\n".join(parts)
                except Exception as e:
                    logging.warning(f"读取完整报告失败: {e}")
            
            if not full_report:
                # 如果无法获取完整报告，使用简单消息
                output_path = result.get("output_path", "")
                full_report = (
                    "跨会议主题分析已完成。\n\n"
                    f"- 输出目录：{output_path}\n"
                    "- 包含内容：统计信息（cross_topic_statistics.md）、"
                    "论文分析（cross_topic_papers/*.md）、总结（cross_topic_summary.md）以及论文列表（papers_list.md）。"
                )
        
        writer(FinalResult(final_report=full_report))

        return Command(
            goto=END,
            update={
                "messages": HumanMessage(
                    content=full_report,
                    name="cross_topic_team",
                )
            },
        )
    except Exception as e:
        logging.exception("跨会议主题分析失败")
        error_msg = f"跨会议主题分析失败：{e}"
        writer(FinalResult(final_report=error_msg))
        return Command(
            goto=END,
            update={
                "messages": HumanMessage(
                    content=error_msg,
                    name="cross_topic_team",
                )
            },
        )


# 构建图
builder = StateGraph(SupervisorState)
builder.add_node(GraphNodeType.SUMMARIZER.value, summarization_node)
builder.add_node(GraphNodeType.SUPERVISOR.value, make_supervisor_node())
builder.add_node(GraphNodeType.CLARIFY_NODE.value, question_clarify_node)
builder.add_node(GraphNodeType.PAPER_TEAM.value, paper_team_node)
builder.add_node(GraphNodeType.CHART_NODE.value, chart_node)
builder.add_node(GraphNodeType.RETRIVAL_TEAM.value, retrival_team_node)
builder.add_node(GraphNodeType.DEEP_RESEARCH_TEAM.value, deep_research_team_node)
builder.add_node(GraphNodeType.CROSS_TOPIC_TEAM.value, cross_topic_team_node)
builder.add_node(GraphNodeType.ANSWER_COMPOSER.value, answer_composer_node)


builder.set_entry_point(GraphNodeType.SUMMARIZER.value)
builder.add_edge(GraphNodeType.SUMMARIZER.value, GraphNodeType.SUPERVISOR.value)
builder.add_edge(GraphNodeType.ANSWER_COMPOSER.value, END)
checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)
