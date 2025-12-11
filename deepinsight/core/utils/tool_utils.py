import logging
import json
from typing import Callable, Awaitable

from google.ai.generativelanguage_v1beta.types import Tool as GenAITool

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import MessageLikeRepresentation, filter_messages, ToolMessage
from langchain.agents.middleware import AgentMiddleware


from deepinsight.core.types.research import ResearchComplete, think_tool
from deepinsight.core.types.graph_config import SearchAPI, RetrievalType
from deepinsight.core.tools.tavily_search import tavily_search
from deepinsight.core.tools.ragflow_retrival import KnowledgeTool
from deepinsight.core.utils.mcp_utils import MCPClientUtils
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.service.rag.engine import RAGEngine


def create_retrieval_tool(retrieval_type: RetrievalType, config: RunnableConfig):
    """Factory function to create retrieval tools based on retrieval type.
    
    Args:
        retrieval_type: The type of retrieval engine (RAGFLOW, LLAMAINDEX, or LIGHTRAG)
        config: Runtime configuration containing retrieval configs
    
    Returns:
        LangChain Tool instance for the specified retrieval type
        
    Raises:
        ValueError: If the retrieval type is not supported or config is missing
    """
    rc = parse_research_config(config)
    retrieval_config = rc.retrieval_config
    
    if not retrieval_config or retrieval_type not in retrieval_config:
        raise ValueError(f"{retrieval_type} retrieval config is not configured.")
    
    # For RAGFlow, use the existing KnowledgeTool
    if retrieval_type == RetrievalType.RAGFLOW:
        return KnowledgeTool.knowledge_retrieve
        
    # For local engines (LlamaIndex, LightRAG), use RAGEngine
    if retrieval_type in [RetrievalType.LLAMAINDEX, RetrievalType.LIGHTRAG]:
        engine = RAGEngine.from_retrieval_config(retrieval_config[retrieval_type])
        return engine.as_tool(retrieval_config[retrieval_type])
    
    raise ValueError(f"Unsupported retrieval type: {retrieval_type}")


async def get_search_tools(search_apis: list[SearchAPI], config: RunnableConfig = None):
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
            # Use the factory to create retrieval tools based on configured retrieval types
            rc = parse_research_config(config)
            retrieval_config = rc.retrieval_config
            # Add all configured retrieval tools
            for retrieval_type in retrieval_config.keys():
                try:
                    retrieval_tool = create_retrieval_tool(retrieval_type, config)
                    tools.append(retrieval_tool)
                except ValueError as e:
                    # Log but continue if a specific retrieval type fails
                    logging.warning(f"Failed to create retrieval tool for {retrieval_type}: {e}")

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
    search_tools = await get_search_tools(search_apis, config)
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

class CoerceToolOutput(AgentMiddleware):
    def wrap_tool_call(
            self,
            request: ToolCallRequest,
            handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        # 1. 先执行实际的工具调用，获取结果
        result = handler(request)

        # 2. 确保 tool_call['args']['messages'] 每项的 content 都是字符串
        if isinstance(request.tool_call.get("args", {}).get("messages"), list):
            for message in request.tool_call["args"]["messages"]:
                if not isinstance(message.get("content"), str):
                    # 添加更细致的类型转换，确保每个 message 的内容都被处理为字符串
                    if isinstance(message["content"], (dict, list)):
                        message["content"] = json.dumps(message["content"], ensure_ascii=False)
                    else:
                        message["content"] = str(message["content"])

        # 3. 处理 ToolMessage 中的消息
        if isinstance(result, ToolMessage):
            if isinstance(result.content, dict):
                # 提取消息列表，如果 content 是字典
                messages = result.content.get("messages", [])
            else:
                messages = []

            # 遍历 messages，确保每个 message 的 content 都是字符串
            for message in messages:
                if not isinstance(message.content, str):
                    if isinstance(message.content, (dict, list)):
                        message.content = json.dumps(message.content, ensure_ascii=False)
                    else:
                        message.content = str(message.content)

            # 4. 最终处理 ToolMessage 的顶层 content
            if not isinstance(result.content, str):
                try:
                    # 尝试将整个内容对象序列化为 JSON 字符串
                    result.content = json.dumps(result.content, ensure_ascii=False)
                except TypeError:
                    # 如果无法序列化，则转为通用的字符串表示
                    result.content = str(result.content)

        return result

    async def wrap_tool_call(
            self,
            request: ToolCallRequest,
            handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        # 1. 先执行实际的工具调用，获取结果
        result = await handler(request)

        # 2. 确保 tool_call['args']['messages'] 每项的 content 都是字符串
        if isinstance(request.tool_call.get("args", {}).get("messages"), list):
            for message in request.tool_call["args"]["messages"]:
                if not isinstance(message.get("content"), str):
                    # 添加更细致的类型转换，确保每个 message 的内容都被处理为字符串
                    if isinstance(message["content"], (dict, list)):
                        message["content"] = json.dumps(message["content"], ensure_ascii=False)
                    else:
                        message["content"] = str(message["content"])

        # 3. 处理 ToolMessage 中的消息
        if isinstance(result, ToolMessage):
            if isinstance(result.content, dict):
                # 提取消息列表，如果 content 是字典
                messages = result.content.get("messages", [])
            else:
                messages = []

            # 遍历 messages，确保每个 message 的 content 都是字符串
            for message in messages:
                if not isinstance(message.content, str):
                    if isinstance(message.content, (dict, list)):
                        message.content = json.dumps(message.content, ensure_ascii=False)
                    else:
                        message.content = str(message.content)

            # 4. 最终处理 ToolMessage 的顶层 content
            if not isinstance(result.content, str):
                try:
                    # 尝试将整个内容对象序列化为 JSON 字符串
                    result.content = json.dumps(result.content, ensure_ascii=False)
                except TypeError:
                    # 如果无法序列化，则转为通用的字符串表示
                    result.content = str(result.content)

        return result

