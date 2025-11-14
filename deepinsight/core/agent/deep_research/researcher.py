import asyncio
import json
import logging
from typing import Any, Annotated, Literal, TypedDict, Dict
import operator
from pydantic import BaseModel

from langchain_core.messages import SystemMessage, ToolMessage, HumanMessage, MessageLikeRepresentation, filter_messages
from langchain_core.runnables import RunnableConfig
from langgraph.constants import START, END
from langgraph.config import get_stream_writer
from langgraph.graph import StateGraph
from langgraph.types import Command

from deepinsight.core.types.research import (
    ErrorResult,
    WebSearchResult,
    ToolType,
    ToolUnifiedResponse,
)


from deepinsight.core.tools.paper_statistic import affiliation_analysis, country_analysis, domain_analysis
from deepinsight.core.utils.research_utils import parse_research_config, override_reducer, dict_merge_reducer
from deepinsight.core.utils.utils import get_today_str
from deepinsight.core.utils.llm_token_utils import (
    is_token_limit_exceeded,
    remove_up_to_last_ai_message,
)
from deepinsight.core.utils.tool_utils import get_all_tools, openai_websearch_called, anthropic_websearch_called

CONFERENCE_FIGURE_TOOLS = [affiliation_analysis.name, country_analysis.name, domain_analysis.name]
TOOLS_SKIP_UNIFIED_STREAM = ["tavily_search", "ResearchComplete", "think_tool"]


class ResearcherState(TypedDict):
    """State for individual researchers conducting research."""
    researcher_messages: Annotated[list[MessageLikeRepresentation], operator.add]
    tool_call_iterations: int = 0
    research_topic: str
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer] = []
    reference_images: Annotated[Dict, dict_merge_reducer] = {}


class ResearcherOutputState(BaseModel):
    """Output state from individual researchers."""

    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer] = []
    reference_images: Annotated[Dict, dict_merge_reducer] = {}

def parse_tool_result(name: str, raw_result: Any):
    if isinstance(raw_result, str):
        try:
            raw_result = json.loads(raw_result)
        except Exception:
            return ErrorResult(error=raw_result)

    if isinstance(raw_result, list):
        return [
            WebSearchResult(
                title=item.get("title"),
                url=item.get("link"),
                icon=item.get("icon")
            )
            for item in raw_result if isinstance(item, dict)
        ]

    return ErrorResult(error=str(raw_result))


# Tool Execution Helper Function
async def execute_tool_safely(tool, args, config, name):
    """Safely execute a tool with error handling."""
    writer = get_stream_writer()
    try:
        result = await tool.ainvoke(args, config)
        if isinstance(result, str):
            try:
                result_object = json.loads(result)
                parsed_tool_result = parse_tool_result(name, result_object)
                if name not in TOOLS_SKIP_UNIFIED_STREAM and parsed_tool_result:
                    unified_response = ToolUnifiedResponse(
                        parent_message_id=config.get("metadata", {}).get("parent_message_id", None),
                        type=ToolType.web_search,
                        name=name,
                        args=args,
                        result=parsed_tool_result,
                    )
                    writer(unified_response)
            except Exception:
                pass
        return result
    except Exception as e:
        error_response = ToolUnifiedResponse(
            parent_message_id=config.get("metadata", {}).get("parent_message_id", None),
            type=ToolType.web_search,
            name=name,
            args=args,
            result=ErrorResult(
                error=f"Error executing tool: {str(e)}"
            )
        )
        writer(error_response)
        return f"Error executing tool: {str(e)}"


async def topic_researcher(state: ResearcherState, config: RunnableConfig) -> Command[Literal["researcher_tools"]]:
    """Individual researcher that conducts focused research on specific topics.

    This researcher is given a specific research topic by the supervisor and uses
    available tools (search, think_tool, MCP tools) to gather comprehensive information.
    It can use think_tool for strategic planning between searches.

    Args:
        state: Current researcher state with messages and topic context
        config: Runtime configuration with model settings and tool availability

    Returns:
        Command to proceed to researcher_tools for tool execution
    """

    # Step 1: Load configuration and validate tool availability
    rc = parse_research_config(config)
    researcher_messages = state.get("researcher_messages", [])

    # Get all available research tools (search, MCP, think_tool)
    tools = await get_all_tools(config)
    if len(tools) == 0:
        raise ValueError(
            "No tools found to conduct research: Please configure either your "
            "search API or add MCP tools to your configuration."
        )

    researcher_prompt_template = rc.prompt_manager.get_prompt(
        name="research_system_prompt",
        group=rc.prompt_group,
    )

    # Prepare system prompt with MCP context if available
    researcher_prompt = researcher_prompt_template.format(
        mcp_prompt="",
        date=get_today_str()
    )

    llm = rc.get_model()
    # Configure model with tools, retry logic, and settings
    research_model = (
        llm
        .bind_tools(tools)
        .with_retry(stop_after_attempt=rc.max_structured_output_retries)
    )

    # Step 3: Generate researcher response with system context
    messages = [SystemMessage(content=researcher_prompt)] + researcher_messages
    response = await research_model.ainvoke(messages)

    # Step 4: Update state and proceed to tool execution
    return Command(
        goto="researcher_tools",
        update={
            "researcher_messages": [response],
            "tool_call_iterations": state.get("tool_call_iterations", 0) + 1
        }
    )


async def topic_tools_exec(state: ResearcherState, config: RunnableConfig) -> Command[
    Literal["researcher", "compress_research"]]:
    """Execute tools called by the researcher, including search tools and strategic thinking.

    This function handles various types of researcher tool calls:
    1. think_tool - Strategic reflection that continues the research conversation
    2. Search tools (tavily_search, web_search) - Information gathering
    3. MCP tools - External tool integrations
    4. ResearchComplete - Signals completion of individual research task

    Args:
        state: Current researcher state with messages and iteration count
        config: Runtime configuration with research limits and tool settings

    Returns:
        Command to either continue research loop or proceed to compression
    """
    try:
        # Step 1: Extract current state and check early exit conditions
        rc = parse_research_config(config)
        researcher_messages = state.get("researcher_messages", [])

        # Check if there are any messages
        if not researcher_messages:
            logging.error("No researcher messages found in state")
            raise ValueError("researcher_messages list is empty")

        most_recent_message = researcher_messages[-1]
        # Early exit if no tool calls were made (including native web search)
        has_tool_calls = bool(most_recent_message.tool_calls)
        has_native_search = (
                openai_websearch_called(most_recent_message) or
                anthropic_websearch_called(most_recent_message)
        )

        logging.debug(f"Tool calls present: {has_tool_calls}, Native search present: {has_native_search}")

        if not has_tool_calls and not has_native_search:
            logging.debug("No tool calls or native search detected, proceeding to compression")
            return Command(goto="compress_research")

        # Step 2: Handle other tool calls (search, MCP tools, etc.)
        tools = await get_all_tools(config)
        tools_by_name = {
            tool.name if hasattr(tool, "name") else tool.get("name", "web_search"): tool
            for tool in tools
        }

        # Execute all tool calls in parallel
        tool_calls = most_recent_message.tool_calls

        tool_execution_tasks = [
            execute_tool_safely(tools_by_name[tool_call["name"]], tool_call["args"], config, tool_call["name"])
            for tool_call in tool_calls
        ]

        observations = await asyncio.gather(*tool_execution_tasks)

        # Create tool messages from execution results
        tool_outputs = []
        reference_images = {}
        for observation, tool_call in zip(observations, tool_calls):
            if ((tool_call["name"] == "tavily_search") and isinstance(observation, dict) and
                    ("formatted_output" in observation)):
                tool_outputs.append(
                    ToolMessage(
                        content=observation["formatted_output"],
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"]
                    )
                )
                reference_images.update(observation.get("reference_images", {}))
            else:
                if ((tool_call["name"] in CONFERENCE_FIGURE_TOOLS) and isinstance(observation, dict) and
                        ("figure_url" in observation)):
                    desc = f"A chart generated by {tool_call['name']!r} with argument {tool_call['args']}"
                    reference_images[observation["figure_url"]] = desc
                tool_outputs.append(
                    ToolMessage(
                        content=observation,
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"]
                    )
                )
        logging.debug(f"Created {len(tool_outputs)} tool output messages")

        # Step 3: Check late exit conditions (after processing tools)
        current_iterations = state.get("tool_call_iterations", 0)
        max_iterations = rc.max_react_tool_calls
        exceeded_iterations = current_iterations >= max_iterations
        research_complete_called = any(
            tool_call["name"] == "ResearchComplete"
            for tool_call in most_recent_message.tool_calls
        )

        logging.debug(
            f"Iteration check: current={current_iterations}, max={max_iterations}, exceeded={exceeded_iterations}")
        logging.debug(f"Research complete called: {research_complete_called}")

        if exceeded_iterations or research_complete_called:
            # End research and proceed to compression
            logging.debug("Research completed or iterations exceeded, proceeding to compression")
            return Command(
                goto="compress_research",
                update={"researcher_messages": tool_outputs, "reference_images": reference_images}
            )

        # Continue research loop with tool results
        logging.debug("Continuing research loop with tool results")
        return Command(
            goto="researcher",
            update={
                "researcher_messages": tool_outputs,
                "reference_images": reference_images
            }
        )

    except ValueError as ve:
        logging.error(f"Value error occurred: {str(ve)}")
        raise  # Re-raise the exception after logging
    except KeyError as ke:
        logging.error(f"Key error occurred: Missing key {str(ke)}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error in researcher_tools: {str(e)}")
        # Log the full exception traceback
        import traceback
        logging.error(f"Exception traceback: {traceback.format_exc()}")
        raise


async def topic_results_compress(state: ResearcherState, config: RunnableConfig):
    """Compress and synthesize research findings into a concise, structured summary.

    This function takes all the research findings, tool outputs, and AI messages from
    a researcher's work and distills them into a clean, comprehensive summary while
    preserving all important information and findings.

    Args:
        state: Current researcher state with accumulated research messages
        config: Runtime configuration with compression model settings

    Returns:
        Dictionary containing compressed research summary and raw notes
    """
    # Step 1: Configure the compression model
    rc = parse_research_config(config)
    llm = rc.get_model()

    # Step 2: Prepare messages for compression
    researcher_messages = state.get("researcher_messages", [])

    # Add instruction to switch from research mode to compression mode
    researcher_messages.append(HumanMessage(
        content=rc.prompt_manager.get_prompt(
            name="compress_research_simple_human_message",
            group=rc.prompt_group,
        ).format()
    ))

    # Step 3: Attempt compression with retry logic for token limit issues
    synthesis_attempts = 0
    max_attempts = 3

    while synthesis_attempts < max_attempts:
        try:
            # Create system prompt focused on compression task

            compression_prompt = rc.prompt_manager.get_prompt(
                name="compress_research_system_prompt",
                group=rc.prompt_group,
            ).format(date=get_today_str())
            messages = [SystemMessage(content=compression_prompt)] + researcher_messages

            # Execute compression
            response = await llm.ainvoke(messages)

            # Extract raw notes from all tool and AI messages
            raw_notes_content = "\n".join([
                str(message.content)
                for message in filter_messages(researcher_messages, include_types=["tool", "ai"])
            ])
            # Return successful compression result
            return {
                "compressed_research": str(response.content),
                "raw_notes": [raw_notes_content],
                "reference_images": state.get("reference_images", {})
            }

        except Exception as e:
            synthesis_attempts += 1
            # Handle token limit exceeded by removing older messages
            if is_token_limit_exceeded(e, llm.name):
                researcher_messages = remove_up_to_last_ai_message(researcher_messages)
                continue
            
            continue

    # Step 4: Return error result if all attempts failed
    raw_notes_content = "\n".join([
        str(message.content)
        for message in filter_messages(researcher_messages, include_types=["tool", "ai"])
    ])

    return {
        "compressed_research": "Error synthesizing research report: Maximum retries exceeded",
        "raw_notes": [raw_notes_content],
    }


# Researcher Subgraph Construction
# Creates individual researcher workflow for conducting focused research on specific topics
graph_builder = StateGraph(
    ResearcherState,
    output_schema=ResearcherOutputState,
)

# Add researcher nodes for research execution and compression
graph_builder.add_node("researcher", topic_researcher)  # Main researcher logic
graph_builder.add_node("researcher_tools", topic_tools_exec)  # Tool execution handler
graph_builder.add_node("compress_research", topic_results_compress)  # Research compression

# Define researcher workflow edges
graph_builder.add_edge(START, "researcher")  # Entry point to researcher
graph_builder.add_edge("compress_research", END)  # Exit point after compression

# Compile researcher subgraph for parallel execution by supervisor
graph = graph_builder.compile()
