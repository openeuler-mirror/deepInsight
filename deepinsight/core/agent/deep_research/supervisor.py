# Main Deep Researcher Graph Construction
# Creates the complete deep research workflow from user input to final report
import asyncio
import logging
import os
from typing import Literal, Dict, Optional, Annotated, TypedDict
from pydantic import BaseModel, Field

from langchain_core.messages import get_buffer_string, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import MessageLikeRepresentation
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.graph import MessagesState
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from deepinsight.core.types.graph_nodes import DeepResearchNodeName
from deepinsight.core.agent.deep_research.researcher import graph as topic_research_subgraph
from deepinsight.core.agent.expert_review.expert_review import build_expert_review_graph
from deepinsight.core.types.research import (
    ResearchComplete, 
    ClarifyNeedUser, 
    WaitResearchBriefEdit, 
    WaitReportOutlineEdit,
    FinalResult,
    think_tool,
)
from deepinsight.core.utils.research_utils import parse_research_config, override_reducer, dict_merge_reducer
from deepinsight.core.tools.best_paper_analysis import batch_analyze_papers
from deepinsight.core.tools.file_system import MemoryMCPFilesystem
from deepinsight.core.tools.keynote_analysis import batch_analyze_keynotes
from deepinsight.core.utils.utils import get_today_str
from deepinsight.core.utils.tool_utils import get_notes_from_tool_calls
from deepinsight.core.utils.llm_token_utils import (
    is_token_limit_exceeded,
    get_model_token_limit,
)


class ConductResearch(BaseModel):
    """Call this tool to conduct research on a specific topic."""
    research_id: Optional[str] = Field(None, description="The topic id, do not need fill in for llm.")
    research_topic: str = Field(
        description="The topic to research. Should be a single topic, and should be described in high detail (at least a paragraph).",
    )


class ClarifyWithUser(BaseModel):
    """Model for user clarification requests."""

    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question.",
    )
    question: str = Field(
        description="A question to ask the user to clarify the report scope",
    )
    verification: str = Field(
        description="Verify message that we will start research after the user has provided the necessary information.",
    )


class AgentInputState(MessagesState):
    """InputState is only 'messages'."""


class AgentState(MessagesState):
    """Main agent state containing messages and research data."""

    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    research_brief: Optional[str]
    raw_notes: Annotated[list[str], override_reducer] = []
    notes: Annotated[list[str], override_reducer] = []
    final_report: str
    final_report_outline: str
    expert_comments: Annotated[Dict[str, str], dict_merge_reducer]
    reference_images: Annotated[Dict, dict_merge_reducer] = {}


class SupervisorState(TypedDict):
    """State for the supervisor that manages research tasks."""

    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    research_brief: str
    notes: Annotated[list[str], override_reducer] = []
    research_iterations: int = 0
    raw_notes: Annotated[list[str], override_reducer] = []
    reference_images: Annotated[Dict, dict_merge_reducer] = {}


async def clarify_with_user(state: AgentState, config: RunnableConfig) -> Command[
    Literal["write_research_brief", "wait_user_clarification"]]:
    """Analyze user messages and ask clarifying questions if the research scope is unclear.

    This function determines whether the user's request needs clarification before proceeding
    with research. If clarification is disabled or not needed, it proceeds directly to research.

    Args:
        state: Current agent state containing user messages
        config: Runtime configuration with model settings and preferences

    Returns:
        Command to either end with a clarifying question or proceed to research brief
    """
    # Step 1: Check if clarification is enabled in configuration
    rc = parse_research_config(config)
    if not rc.allow_user_clarification:
        # Skip clarification step and proceed directly to research
        return Command(goto="write_research_brief")

    # Step 2: Prepare the model for structured clarification analysis
    messages = state["messages"]

    # Configure model with structured output and retry logic
    llm = rc.get_model()

    # Step 3: Analyze whether clarification is needed
    prompt_tpl = rc.prompt_manager.get_prompt(
            name="clarify_with_user_instructions",
            group=rc.prompt_group,
    )
    parser = PydanticOutputParser(pydantic_object=ClarifyWithUser)
    sys_msgs = prompt_tpl.format_messages(messages=get_buffer_string(messages), date=get_today_str())
    sys_content = sys_msgs[0].content if sys_msgs else ""
    system_message = SystemMessage(content=sys_content + "\n\n---\n" + parser.get_format_instructions())
    chain = llm | parser
    result = await chain.with_retry().ainvoke(input=[system_message] + messages)

    # Step 4: Route based on clarification analysis
    if result.need_clarification:
        # End with clarifying question for user
        return Command(
            goto="wait_user_clarification",
            update={"messages": [AIMessage(content=result.question)]}
        )
    else:
        # Proceed to research with verification message
        return Command(
            goto="write_research_brief",
            update={"messages": [AIMessage(content=result.verification)]}
        )


async def wait_user_clarification(state: AgentState):
    user_reply = interrupt(ClarifyNeedUser(question=state["messages"][-1].content))
    return {
        "messages": [HumanMessage(content=user_reply)]
    }


async def write_research_brief(state: AgentState, config: RunnableConfig):
    """Transform user messages into a structured research brief and initialize supervisor."""
    # Step 1: Set up the research model (without structured output)
    rc = parse_research_config(config)

    llm = rc.get_model()

    # Step 2: Generate structured research brief from user messages
    prompt_name = "transform_messages_into_research_topic_prompt"
    if rc.expert_name:
        prompt_name = f"{prompt_name}_{rc.expert_name}"
    try:
        prompt_content = rc.prompt_manager.get_prompt(
            name=prompt_name,
            group=rc.prompt_group,
        )
    except Exception as e:
        logging.error(f"Write research brief can't load expert {rc.expert_name} prompt, {e}")
        prompt_content = rc.prompt_manager.get_prompt(
            name="transform_messages_into_research_topic_prompt",
            group=rc.prompt_group,
        )
    # 获取原始文本响应
    chain = prompt_content | llm
    response_msg = await chain.ainvoke(
        dict(
            messages=get_buffer_string(state.get("messages", [])),
            date=get_today_str()
        )
    )
    response_text = response_msg.content
    return {
        "research_brief": response_text,
        "supervisor_messages": {
            "type": "override",
            "value": [
                HumanMessage(content=response_text)
            ]
        }
    }


async def wait_user_confirm_research_brief(state: AgentState, config: RunnableConfig):
    # Step 1: Set up the research model (without structured output)
    rc = parse_research_config(config)
    # Step 2: Initialize supervisor with research brief and instructions
    prompt = rc.prompt_manager.get_prompt(
            name="lead_researcher_prompt",
            group=rc.prompt_group,
    ).format(
        date=get_today_str(),
        max_concurrent_research_units=rc.max_concurrent_research_units,
        max_researcher_iterations=rc.max_researcher_iterations
    )

    if not rc.allow_edit_research_brief:
        return {
            "research_brief": state["research_brief"],
            "supervisor_messages": {
                "type": "override",
                "value": [
                    SystemMessage(content=prompt),
                    HumanMessage(content=state["research_brief"])
                ]
            }
        }
    user_reply = interrupt(WaitResearchBriefEdit(research_brief=state["research_brief"]))

    return {
        "research_brief": user_reply,
        "supervisor_messages": {
            "type": "override",
            "value": [
                SystemMessage(content=prompt),
                HumanMessage(content=user_reply)
            ]
        }
    }


async def generate_report_outline(state: AgentState, config: RunnableConfig):
    # Step 1: Extract research findings and prepare state cleanup
    notes = state.get("notes", [])
    cleared_state = {"notes": {"type": "override", "value": []}}
    findings = "\n".join(notes)

    # Step 2: Configure the final report generation model
    rc = parse_research_config(config)

    # Step 3: Attempt report generation with token limit retry logic
    max_retries = 3
    current_retry = 0
    findings_token_limit = None
    llm = rc.get_model()
    while current_retry <= max_retries:
        try:
            # Create comprehensive prompt with all research context
            prompt = rc.prompt_manager.get_prompt(
                    name="final_report_outline_generation_prompt",
                    group=rc.prompt_group,
            )
            
            chain = prompt | llm
            # Generate the final report outline
            final_report_outline = await chain.ainvoke(
                dict(
                    research_brief=state.get("research_brief", ""),
                    messages=get_buffer_string(state.get("messages", [])),
                    findings=findings,
                    date=get_today_str()
                )
            )
            return {
                "final_report_outline": final_report_outline.content,
                # "messages": [final_report_outline],
                **cleared_state
            }

        except Exception as e:
            # Handle token limit exceeded errors with progressive truncation
            if is_token_limit_exceeded(e, llm):
                current_retry += 1

                if current_retry == 1:
                    # First retry: determine initial truncation limit
                    model_token_limit = get_model_token_limit(llm.name)
                    if not model_token_limit:
                        return {
                            "final_report_outline": f"Error generating final report outline: Token limit exceeded, however, we could not determine the model's maximum context length. Please update the model map in deep_researcher/utils.py with this information. {e}",
                            "messages": [AIMessage(content="Report outline generation failed due to token limits")],
                            **cleared_state
                        }
                    # Use 4x token limit as character approximation for truncation
                    findings_token_limit = model_token_limit * 4
                else:
                    # Subsequent retries: reduce by 10% each time
                    findings_token_limit = int(findings_token_limit * 0.9)

                # Truncate findings and retry
                findings = findings[:findings_token_limit]
                continue
            else:
                # Non-token-limit error: return error immediately
                return {
                    "final_report_outline": f"Error generating final report outline: {e}",
                    "messages": [AIMessage(content="Report outline generation failed due to an error")],
                    **cleared_state
                }

    # Step 4: Return failure result if all retries exhausted
    return {
        "final_report_outline": "Error generating final report: Maximum retries exceeded",
        "messages": [AIMessage(content="Report outline generation failed after maximum retries")],
        **cleared_state
    }


async def wait_user_confirm_report_outline(state: AgentState, config: RunnableConfig):
    rc = parse_research_config(config)
    if not rc.allow_edit_report_outline:
        return {
            "final_report_outline": state["final_report_outline"]
        }
    user_reply = interrupt(WaitReportOutlineEdit(report_outline=state["final_report_outline"]))
    return {
        "final_report_outline": user_reply,
    }


async def wait_user_edit_report_outline(state: AgentState, config: RunnableConfig):
    rc = parse_research_config(config)
    if not rc.allow_edit_report_outline:
        return {
            "final_report_outline": state["final_report_outline"]
        }
    user_reply = interrupt(WaitReportOutlineEdit(report_outline=state["final_report_outline"]))
    return {
        "final_report_outline": user_reply,
    }


async def final_report_generation(state: AgentState, config: RunnableConfig):
    """Generate the final comprehensive research report with retry logic for token limits.

    This function takes all collected research findings and synthesizes them into a
    well-structured, comprehensive final report using the configured report generation model.

    Args:
        state: Agent state containing research findings and context
        config: Runtime configuration with model settings and API keys

    Returns:
        Dictionary containing the final report and cleared state
    """
    # Step 1: Extract research findings and prepare state cleanup
    fs_instance = MemoryMCPFilesystem()
    notes = state.get("notes", [])
    cleared_state = {"notes": {"type": "override", "value": []}}
    findings = "\n".join(notes)

    # Step 2: Configure the final report generation model
    rc = parse_research_config(config)

    # Step 3: Attempt report generation with token limit retry logic
    max_retries = 3
    current_retry = 0
    findings_token_limit = None
    llm = rc.get_model()
    while current_retry <= max_retries:
        try:
            # Create comprehensive prompt with all research context
            prompt = rc.prompt_manager.get_prompt(
                    name="final_report_generation_prompt",
                    group=rc.prompt_group,
            )

            chain = prompt | llm
            # Generate the final report
            final_report = await chain.ainvoke(
                dict(
                    research_brief=state.get("research_brief", ""),
                    messages=get_buffer_string(state.get("messages", [])),
                    findings=findings,
                    date=get_today_str(),
                    final_report_outline=state.get("final_report_outline", ""),
                    reference_images=state.get("reference_images", ""),
                )
            )

            fs_instance = MemoryMCPFilesystem()
            # 必须提前创建好目录，大模型在使用过程中会查询目录存在不存在，不存在则报错
            fs_instance.create_folders(f"/{rc.run_id}",
                                       ["conference_best_papers", "conference_keynotes"])
            if rc.prompt_group == "conference_keynotes":
                output_file = f"/{str(rc.run_id)}/{rc.prompt_group}"
                from langchain.agents import create_agent
                logging.debug(f"final_report:{final_report}, output_file: {output_file}")
                try:
                    system_prompt = """
                    - Role: 学术会议 Keynotes 分析架构师
                    - Task: 执行用户输入的 Keynotes分析，调用专用工具分析,请不要反问用户任何问题。
                    """
                    agent = create_agent(
                        model=llm,
                        tools=[batch_analyze_keynotes],
                        system_prompt=system_prompt
                    )
                    agent.invoke(
                        {"messages": [{"role": "user",
                                       "content": f"请分析以下keynotes，不要反问我任何内容，keynotes的集合如下：{final_report.content},"
                                                  f" 分析后结果保存到如下文件夹：{output_file}"}]},
                    )

                except Exception as e:
                    logging.error(f"keynote分析失败: {final_report}, 错误: {e}")
                    import traceback
                    traceback.print_exc()  # 打印堆栈信息

                keynotes_files_content_map = fs_instance.read_all_files_in_dir(output_file)
                keynotes_content = "\n".join(content for _, content in keynotes_files_content_map.items())
                logging.debug(f"keynotes_content: {keynotes_content}")
                final_report.content = keynotes_content
            elif rc.prompt_group == "conference_best_papers":
                from langchain.agents import create_agent
                output_file = f"/{str(rc.run_id)}/{rc.prompt_group}"
                agent = create_agent(
                    model=llm,
                    tools=[batch_analyze_papers]
                )
                agent.invoke(
                    {"messages": [{"role": "user",
                                   "content": f"论文集合相关信息如下，请批量对如下论文进行分析：/{final_report}, 论文保存路径：{output_file}"}]},
                )
                paper_files_content_map = fs_instance.read_all_files_in_dir(output_file)
                final_report.content = "\n\n".join(content for _, content in paper_files_content_map.items())

                # output_dir = f"/{str(rc.run_id)}/"
                # # Use configured work_root; fallback to current working directory
                # work_root = getattr(rc, "work_root", None)
                # if not work_root:
                #     work_root = os.getcwd()
                # output_path = os.path.join(work_root, "conference_report_result", rc.thread_id)
                # fs_instance.export_to_real_fs(real_dir=output_path, folder_path=output_dir)


            output_file = f"/{str(rc.run_id)}/{rc.prompt_group}.md"
            # example: 如何将最终结果写入到临时文件
            fs_instance.write_file(file_path=f"{output_file}", content=final_report.content)

            return {
                "final_report": final_report.content,
                "messages": [final_report],
                "output_dir": output_file,
                **cleared_state
            }

        except Exception as e:
            # Handle token limit exceeded errors with progressive truncation
            if is_token_limit_exceeded(e, llm):
                current_retry += 1

                if current_retry == 1:
                    # First retry: determine initial truncation limit
                    model_token_limit = get_model_token_limit(llm.name)
                    if not model_token_limit:
                        return {
                            "final_report": f"Error generating final report: Token limit exceeded, however, we could not determine the model's maximum context length. Please update the model map in deep_researcher/utils.py with this information. {e}",
                            "messages": [AIMessage(content="Report generation failed due to token limits")],
                            **cleared_state
                        }
                    # Use 4x token limit as character approximation for truncation
                    findings_token_limit = model_token_limit * 4
                else:
                    # Subsequent retries: reduce by 10% each time
                    findings_token_limit = int(findings_token_limit * 0.9)

                # Truncate findings and retry
                findings = findings[:findings_token_limit]
                continue
            else:
                # Non-token-limit error: return error immediately
                return {
                    "final_report": f"Error generating final report: {e}",
                    "messages": [AIMessage(content="Report generation failed due to an error")],
                    **cleared_state
                }

    # Step 4: Return failure result if all retries exhausted
    return {
        "final_report": "Error generating final report: Maximum retries exceeded",
        "messages": [AIMessage(content="Report generation failed after maximum retries")],
        **cleared_state
    }


async def review_by_expert(state: AgentState, config: RunnableConfig):
    rc = parse_research_config(config)
    if not rc.expert_defs:
        logging.error("Enable expert review, but not config expert defs")
        return {}
    export_review_subgraph = build_expert_review_graph(rc.expert_defs)
    result = await export_review_subgraph.ainvoke(dict(
        final_report=state["final_report"]
    ))
    return {
        "expert_comments": result["expert_comments"]
    }


async def publish_result(state: AgentState, config: RunnableConfig):
    rc = parse_research_config(config)
    allow_publish_result = rc.allow_publish_result
    if not allow_publish_result:
        return state
    writer = get_stream_writer()
    writer(FinalResult(
        final_report=state["final_report"],
        expert_review_comments=state["expert_comments"]
    ))
    return state


async def supervisor(state: SupervisorState, config: RunnableConfig) -> Command[Literal["supervisor_tools"]]:
    """Lead research supervisor that plans research strategy and delegates to researchers.

    The supervisor analyzes the research brief and decides how to break down the research
    into manageable tasks. It can use think_tool for strategic planning, ConductResearch
    to delegate tasks to sub-researchers, or ResearchComplete when satisfied with findings.

    Args:
        state: Current supervisor state with messages and research context
        config: Runtime configuration with model settings

    Returns:
        Command to proceed to supervisor_tools for tool execution
    """
    rc = parse_research_config(config)

    # Available tools: research delegation, completion signaling, and strategic thinking
    lead_researcher_tools = [ConductResearch, ResearchComplete, think_tool]

    llm = rc.get_model()

    # Configure model with tools, retry logic, and model settings
    research_model = (
        llm
        .bind_tools(lead_researcher_tools)
        .with_retry()
    )

    # Step 2: Generate supervisor response based on current context

    supervisor_messages = state.get("supervisor_messages", [])
    response = await research_model.ainvoke(supervisor_messages)

    # Step 3: Update state and proceed to tool execution
    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1
        }
    )


async def supervisor_tools(state: SupervisorState, config: RunnableConfig) -> Command[Literal["supervisor", "__end__"]]:
    """Execute tools called by the supervisor, including research delegation and strategic thinking.

    This function handles three types of supervisor tool calls:
    1. think_tool - Strategic reflection that continues the conversation
    2. ConductResearch - Delegates research tasks to sub-researchers
    3. ResearchComplete - Signals completion of research phase

    Args:
        state: Current supervisor state with messages and iteration count
        config: Runtime configuration with research limits and model settings

    Returns:
        Command to either continue supervision loop or end research phase
    """
    # Step 1: Extract current state and check exit conditions
    rc = parse_research_config(config)

    llm = rc.get_model()
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1]

    # Define exit criteria for research phase
    exceeded_allowed_iterations = research_iterations > rc.max_researcher_iterations
    no_tool_calls = not most_recent_message.tool_calls
    research_complete_tool_call = any(
        tool_call["name"] == "ResearchComplete"
        for tool_call in most_recent_message.tool_calls
    )

    # Exit if any termination condition is met
    if exceeded_allowed_iterations or no_tool_calls or research_complete_tool_call:
        return Command(
            goto=END,
            update={
                "notes": get_notes_from_tool_calls(supervisor_messages),
                "research_brief": state.get("research_brief", "")
            }
        )

    # Step 2: Process all tool calls together (both think_tool and ConductResearch)
    all_tool_messages = []
    update_payload = {"supervisor_messages": [], "reference_images": {}}

    # Handle think_tool calls (strategic reflection)
    think_tool_calls = [
        tool_call for tool_call in most_recent_message.tool_calls
        if tool_call["name"] == "think_tool"
    ]

    for tool_call in think_tool_calls:
        reflection_content = tool_call["args"]["reflection"]
        prompt_group: str = rc.prompt_group
        if prompt_group == "conference_best_papers":
            research_brief = state['research_brief']
            all_tool_messages.append(ToolMessage(
                content=f"research_brief：{research_brief},请严格遵守研究列表，不要遗漏任意步骤，Reflection recorded: {reflection_content}",
                name="think_tool",
                tool_call_id=tool_call["id"]
            ))
        else:
            all_tool_messages.append(ToolMessage(
                content=f"Reflection recorded: {reflection_content}",
                name="think_tool",
                tool_call_id=tool_call["id"]
            ))

    # Handle ConductResearch calls (research delegation)
    conduct_research_calls = [
        tool_call for tool_call in most_recent_message.tool_calls
        if tool_call["name"] == "ConductResearch"
    ]

    for each in conduct_research_calls:
        each["args"]["research_id"] = each["id"]

    if conduct_research_calls:
        try:
            # Limit concurrent research units to prevent resource exhaustion
            allowed_conduct_research_calls = conduct_research_calls[:rc.max_concurrent_research_units]
            overflow_conduct_research_calls = conduct_research_calls[rc.max_concurrent_research_units:]

            # Execute research tasks in parallel
            research_tasks = []
            for tool_call in allowed_conduct_research_calls:
                research_tasks.append(
                    topic_research_subgraph.with_config(
                        config={
                            "parent_message_id": tool_call["args"]["research_id"],
                        }
                    )
                    .ainvoke({
                        "researcher_messages": [
                            HumanMessage(content=tool_call["args"]["research_topic"])
                        ],
                        "research_topic": tool_call["args"]["research_topic"]
                    })
                )

            tool_results = await asyncio.gather(*research_tasks)

            # Create tool messages with research results
            for observation, tool_call in zip(tool_results, allowed_conduct_research_calls):
                all_tool_messages.append(ToolMessage(
                    content=observation.get("compressed_research", "Error synthesizing research report: Maximum "
                                                                   "retries exceeded"),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"]
                ))
                update_payload["reference_images"].update(observation.get("reference_images", {}))

            # Handle overflow research calls with error messages
            for overflow_call in overflow_conduct_research_calls:
                all_tool_messages.append(ToolMessage(
                    content=f"Error: Did not run this research as you have already exceeded the maximum number of "
                            f"concurrent research units. Please try again with "
                            f"{rc.max_concurrent_research_units} or fewer research units.",
                    name="ConductResearch",
                    tool_call_id=overflow_call["id"]
                ))

            # Aggregate raw notes from all research results
            raw_notes_concat = "\n".join([
                "\n".join(observation.get("raw_notes", []))
                for observation in tool_results
            ])

            if raw_notes_concat:
                update_payload["raw_notes"] = [raw_notes_concat]

        except Exception as e:
            logging.error(f"Call sub graph error: {e}")
            # Handle research execution errors
            if is_token_limit_exceeded(e, llm.name) or True:
                # Token limit exceeded or other error - end research phase
                return Command(
                    goto=END,
                    update={
                        "notes": get_notes_from_tool_calls(supervisor_messages),
                        "research_brief": state.get("research_brief", "")
                    }
                )

    # Step 3: Return command with all tool results
    update_payload["supervisor_messages"] = all_tool_messages
    return Command(
        goto="supervisor",
        update=update_payload
    )


# Supervisor Subgraph Construction
# Creates the supervisor workflow that manages research delegation and coordination
supervisor_builder = StateGraph(SupervisorState)

# Add supervisor nodes for research management
supervisor_builder.add_node("supervisor", supervisor)  # Main supervisor logic
supervisor_builder.add_node("supervisor_tools", supervisor_tools)  # Tool execution handler

# Define supervisor workflow edges
supervisor_builder.add_edge(START, "supervisor")  # Entry point to supervisor

# Compile supervisor subgraph for use in main workflow
supervisor_subgraph = supervisor_builder.compile()

checkpointer = InMemorySaver()

# Main Deep Researcher Graph Construction
# Creates the complete deep research workflow from user input to final report
graph_builder = StateGraph(
    AgentState,
    input_schema=AgentInputState,
)

# Add main workflow nodes for the complete research process
graph_builder.add_node("clarify_with_user", clarify_with_user)  # User clarification phase
graph_builder.add_node("wait_user_clarification", wait_user_clarification)  # Wait user clarification phase
graph_builder.add_node("write_research_brief", write_research_brief)  # Research planning phase
graph_builder.add_node("wait_user_confirm_research_brief",
                                 wait_user_confirm_research_brief)  # Research planning phase
graph_builder.add_node("research_supervisor", supervisor_subgraph)  # Research execution phase
graph_builder.add_node(DeepResearchNodeName.GENERATE_REPORT_OUTLINE,
                                 generate_report_outline)  # Report outline generation phase
graph_builder.add_node("wait_user_confirm_report_outline",
                                 wait_user_confirm_report_outline)  # Report outline generation phase
graph_builder.add_node(DeepResearchNodeName.GENERATE_REPORT, final_report_generation)  # Report generation phase
graph_builder.add_node("review_by_expert", review_by_expert)  # Expert review phase
graph_builder.add_node("publish_result", publish_result)

# Define main workflow edges for sequential execution
graph_builder.add_edge(START, "clarify_with_user")  # Entry point
graph_builder.add_edge("wait_user_clarification", "write_research_brief")
graph_builder.add_edge("write_research_brief", "wait_user_confirm_research_brief")
graph_builder.add_edge("wait_user_confirm_research_brief", DeepResearchNodeName.GENERATE_REPORT_OUTLINE)
graph_builder.add_edge(DeepResearchNodeName.GENERATE_REPORT_OUTLINE, "wait_user_confirm_report_outline")
graph_builder.add_edge("wait_user_confirm_report_outline", "research_supervisor")
graph_builder.add_edge("research_supervisor", DeepResearchNodeName.GENERATE_REPORT)


def after_report_generation_to(state: AgentState, config: RunnableConfig):
    rc = parse_research_config(config)
    if rc.enable_expert_review:
        return "review_by_expert"
    else:
        return "publish_result"


graph_builder.add_conditional_edges(DeepResearchNodeName.GENERATE_REPORT, after_report_generation_to)
graph_builder.add_edge("review_by_expert", "publish_result")
graph_builder.add_edge("publish_result", END)

# Compile the complete deep researcher workflow
graph = graph_builder.compile(checkpointer=checkpointer)
