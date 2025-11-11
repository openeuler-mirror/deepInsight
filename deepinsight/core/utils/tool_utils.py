from google.ai.generativelanguage_v1beta.types import Tool as GenAITool

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_core.messages import MessageLikeRepresentation, filter_messages


from deepinsight.core.types.research import ResearchComplete, think_tool
from deepinsight.core.types.graph_config import SearchAPI
from deepinsight.core.tools.tavily_search import tavily_search
from deepinsight.core.utils.mcp_utils import MCPClientUtils
from deepinsight.core.utils.research_utils import parse_research_config

async def get_search_tools(search_apis: list[SearchAPI]):
    """Configure and return search tools based on the specified API providers.

    Args:
        search_apis: List of search API providers to use (Anthropic, OpenAI, Tavily, etc.)

    Returns:
        Combined list of configured search tool objects for all specified providers
    """
    tools = []

    for api in search_apis:
        if api == SearchAPI.ANTHROPIC:
            # Anthropic's native web search with usage limits
            tools.append({
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5
            })

        elif api == SearchAPI.GEMINI:
            # Gemini's google search functionality
            tools.append(GenAITool(google_search={}))
            # tools.append({"type": "google_search"})

        elif api == SearchAPI.OPENAI:
            # OpenAI's web search preview functionality
            tools.append({"type": "web_search_preview"})

        elif api == SearchAPI.TAVILY:
            # Configure Tavily search tool with metadata
            search_tool = tavily_search
            search_tool.metadata = {
                **(search_tool.metadata or {}),
                "type": "search",
                "name": "web_search"
            }
            tools.append(search_tool)
        elif api == SearchAPI.PAPER_STATIC_DATA:
            query_tools = await MCPClientUtils.get_tools(
                tools_name_list=["get_institution_stats", "get_proceedings_keyword_frequency",
                                 "get_author_coauthorship",
                                 "generate_bar_chart"],
                server_name="conference-static")
            tools.extend(query_tools)
        elif api == SearchAPI.RAG_RETRIVAL:
            pass
            # tools.append(KnowledgeTool.knowledge_retrieve)

    return tools


async def get_all_tools(config: RunnableConfig):
    """Assemble complete toolkit including research, search, and MCP tools.

    Args:
        config: Runtime configuration specifying search API and MCP settings

    Returns:
        List of all configured and available tools for research operations
    """
    tools = [tool(ResearchComplete), think_tool]

    # Add configured search tools
    rc = parse_research_config(config)
    search_apis = [SearchAPI(value) for value in rc.search_api]
    search_tools = await get_search_tools(search_apis)
    tools.extend(search_tools)

    # Add service-configured LangChain tools from ResearchConfig
    if hasattr(rc, "tools") and rc.tools:
        tools.extend(rc.tools)

    # Track existing tool names to prevent conflicts
    # existing_tool_names = {
    #     tool.name if hasattr(tool, "name") else tool.get("name", "web_search")
    #     for tool in tools
    # }

    # Add MCP tools if configured
    # mcp_tools = await load_mcp_tools(config, existing_tool_names)
    # tools.extend(mcp_tools)

    return tools


def get_notes_from_tool_calls(messages: list[MessageLikeRepresentation]):
    """Extract notes from tool call messages."""
    return [tool_msg.content for tool_msg in filter_messages(messages, include_types="tool")]


def anthropic_websearch_called(response):
    """Detect if Anthropic's native web search was used in the response.

    Args:
        response: The response object from Anthropic's API

    Returns:
        True if web search was called, False otherwise
    """
    try:
        # Navigate through the response metadata structure
        usage = response.response_metadata.get("usage")
        if not usage:
            return False

        # Check for server-side tool usage information
        server_tool_use = usage.get("server_tool_use")
        if not server_tool_use:
            return False

        # Look for web search request count
        web_search_requests = server_tool_use.get("web_search_requests")
        if web_search_requests is None:
            return False

        # Return True if any web search requests were made
        return web_search_requests > 0

    except (AttributeError, TypeError):
        # Handle cases where response structure is unexpected
        return False


def openai_websearch_called(response):
    """Detect if OpenAI's web search functionality was used in the response.

    Args:
        response: The response object from OpenAI's API

    Returns:
        True if web search was called, False otherwise
    """
    # Check for tool outputs in the response metadata
    tool_outputs = response.additional_kwargs.get("tool_outputs")
    if not tool_outputs:
        return False

    # Look for web search calls in the tool outputs
    for tool_output in tool_outputs:
        if tool_output.get("type") == "web_search_call":
            return True

    return False
