import os
import asyncio
import logging
from typing import List, Annotated, Literal

from langchain_core.tools import InjectedToolArg, tool
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.config import get_stream_writer
from langchain_core.messages import HumanMessage

from deepinsight.core.types.graph_config import ResearchConfig
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.research import (
    ErrorResult,
    WebSearchResult, 
    ToolType, 
    ToolUnifiedResponse,
    Summary,
)
from deepinsight.core.utils.utils import get_today_str
from deepinsight.utils.tavily_manager import tavily_key_manager, TavilyBaseKeyManager, SingleKeyManager

TAVILY_SEARCH_DESCRIPTION = (
    "A search engine optimized for comprehensive, accurate, and trusted results. "
    "Useful for when you need to answer questions about current events."
)

def get_tavily_manager(config: RunnableConfig) -> TavilyBaseKeyManager:
    """Get Tavily API key from environment or config."""
    should_get_from_config = os.getenv("GET_API_KEYS_FROM_CONFIG", "false")
    if should_get_from_config.lower() == "true":
        api_key = config.get("configurable", {}).get("apiKeys", {}).get("TAVILY_API_KEY")
        return SingleKeyManager(api_key)  # no available key will raise by its `__init__`
    else:
        return tavily_key_manager()

async def tavily_search_async(
        search_queries,
        max_results: int = 5,
        topic: Literal["general", "news", "finance"] = "general",
        include_raw_content: bool = True,
        config: RunnableConfig = None
):
    """Execute multiple Tavily search queries asynchronously.

    Args:
        search_queries: List of search query strings to execute
        max_results: Maximum number of results per query
        topic: Topic category for filtering results
        include_raw_content: Whether to include full webpage content
        config: Runtime configuration for API key access

    Returns:
        List of search result dictionaries from Tavily API
    """
    # Initialize the Tavily client with API key from config
    tavily_tool = get_tavily_manager(config).tool()
    tavily_tool.max_results = max_results
    tavily_tool.include_raw_content = include_raw_content

    # Create search tasks for parallel execution
    search_tasks = [
        tavily_tool.search_async(
            query,
            topic=topic,
            include_favicon=True,
            search_depth="advanced",
            include_images=True,
            include_image_descriptions=True,
        ) for query in search_queries
    ]
    # Execute all search queries in parallel and return results
    results_or_errors = await asyncio.gather(*search_tasks, return_exceptions=True)
    valid_results = []
    for item in results_or_errors:
        if isinstance(item, BaseException):
            logging.error(f"Tavily search error: {type(item).__name__}: {item}")
            raise item
        valid_results.append(item)
    return valid_results


async def summarize_webpage(model: BaseChatModel, webpage_content: str, rc: ResearchConfig) -> str:
    """Summarize webpage content using AI model with timeout protection.

    Args:
        model: The chat model configured for summarization
        webpage_content: Raw webpage content to be summarized

    Returns:
        Formatted summary with key excerpts, or original content if summarization fails
    """
    try:
        # Create prompt with current date context
        prompt_content = rc.prompt_manager.get_prompt(
                name="summarize_webpage_prompt",
                group=rc.prompt_group,
        ).format(
            webpage_content=webpage_content,
            date=get_today_str()
        )

        parser = PydanticOutputParser(pydantic_object=Summary)
        prompt_content = prompt_content + "\n\n---\n" + parser.get_format_instructions()

        chain = model | parser
        # Execute summarization with timeout to prevent hanging
        summary = await asyncio.wait_for(
            chain.ainvoke([HumanMessage(content=prompt_content)]),
            timeout=60.0  # 60 second timeout for summarization
        )

        # Format the summary with structured sections
        formatted_summary = (
            f"<summary>\n{summary.summary}\n</summary>\n\n"
            f"<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
        )

        return formatted_summary

    except asyncio.TimeoutError:
        # Timeout during summarization - return original content
        logging.warning("Summarization timed out after 60 seconds, returning original content")
        return webpage_content
    except Exception as e:
        # Other errors during summarization - log and return original content
        logging.warning(f"Summarization failed with error: {str(e)}, returning original content")
        return webpage_content


@tool(description=TAVILY_SEARCH_DESCRIPTION)
async def tavily_search(
        queries: List[str],
        topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
        config: RunnableConfig = None
) -> str:
    """Fetch and summarize search results from Tavily search API.

    Args:
        queries: List of search queries to execute
        topic: Topic filter for search results (general, news, or finance)
        config: Runtime configuration for API keys and model settings

    Returns:
        Formatted string containing summarized search results
    """
    # Step 1: Execute search queries asynchronously
    try:
        search_results = await tavily_search_async(
            queries,
            max_results=1,
            topic=topic,
            include_raw_content=True,
            config=config
        )
    except Exception as e:
        error_message = f"Tavily search failed with error: {type(e).__name__}: {e}"
        logging.error(error_message)
        writer = get_stream_writer()
        writer(ToolUnifiedResponse(
            parent_message_id=config.get("metadata", {}).get("parent_message_id", None),
            type=ToolType.web_search,
            name="tavily_search",
            args={"queries": queries},
            result=ErrorResult(
                error=error_message
            )
        ))
        return error_message

    # Step 2: Deduplicate results by URL to avoid processing the same content multiple times
    unique_results = {}
    reference_images = {}
    for response in search_results:
        try:
            for result in (response.get('results') or []):
                url = result.get('url')
                if not url:
                    continue
                if url not in unique_results:
                    unique_results[url] = {**result, "query": response.get('query')}
            images = response.get("images", [])
            if images:
                for idx, img in enumerate(images, 1):
                    description = img.get("description") or "No description provided."
                    reference_images[f"{img['url']}"] = description

        except Exception as parse_err:
            logging.error(f"Parse Tavily response failed: {type(parse_err).__name__}: {parse_err}")

    # Send tool call result
    writer = get_stream_writer()
    writer(ToolUnifiedResponse(
        parent_message_id=config.get("metadata", {}).get("parent_message_id", None),
        type=ToolType.web_search,
        name="tavily_search",
        args={"queries": queries},
        result=[
            WebSearchResult(
                title=res['title'], 
                url=url,
                icon=res.get('favicon', None)
            )
            for url, res in unique_results.items()
        ]
    ))
    
    # Step 3: Set up the summarization model with configuration
    rc = parse_research_config(config)

    # Character limit to stay within model token limits (configurable)
    max_char_to_include = rc.max_content_length

    # Initialize summarization model with retry logic
    # model_api_key = get_api_key_for_model(configurable.summarization_model, config)
    summarization_model = rc.default_model

    # Step 4: Create summarization tasks (skip empty content)
    async def noop():
        """No-op function for results without raw content."""
        return None

    summarization_tasks = [
        noop() if not result.get("raw_content")
        else summarize_webpage(
            summarization_model,
            result['raw_content'][:max_char_to_include],
            rc
        )
        for result in unique_results.values()
    ]

    # Step 5: Execute all summarization tasks in parallel
    summaries = await asyncio.gather(*summarization_tasks)

    # Step 6: Combine results with their summaries
    summarized_results = {
        url: {
            'title': result['title'],
            'content': result['content'] if summary is None else summary,
        }
        for url, result, summary in zip(
            unique_results.keys(),
            unique_results.values(),
            summaries
        )
    }

    # Step 7: Format the final output
    if not summarized_results:
        return "No valid search results found. Please try different search queries or use a different search API."

    formatted_output = "Search results: \n\n"
    for i, (url, result) in enumerate(summarized_results.items()):
        formatted_output += f"\n\n--- SOURCE {i + 1}: {result['title']} ---\n"
        formatted_output += f"URL: {url}\n\n"
        formatted_output += f"SUMMARY:\n{result['content']}\n\n"
        formatted_output += "\n\n" + "-" * 80 + "\n"
    
    if reference_images:
        formatted_output += "RELATED IMAGES:\n"
        for idx, img in enumerate(reference_images.items(), 1):
            url, description = img
            formatted_output += f"  [{idx}] {url}\n      ↳ {description}\n"
        formatted_output += "\n"

    return dict(
        formatted_output=formatted_output,
        reference_images=reference_images
    )

