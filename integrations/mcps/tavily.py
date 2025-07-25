# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import argparse
import json
import logging
import os
import random
from time import sleep
from typing import Annotated, Literal

import requests
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from pydantic import Field

from deepinsight.utils.parallel_worker_utils import Executor
from integrations.mcps.utils import check_env_vars

# --- 该工具需要统一配置环境变量，增加如下变量 ---
# 'TAVILY_API_KEY'
# 'TAVILY_PORT' 非必须，默认 "0.0.0.0"
# 'TAVILY_HOST' 非必须，默认 "8081"
# 'TAVILY_BASE_URL' 非必须，默认 "https://api.tavily.com"

# 默认值
DEFAULT_EXCLUDE_DOMAINS = ["csdn.net", "blog.csdn.net"]
DEFAULT_SEARCH_DEPTH = "advanced"
DEFAULT_MAX_RESULTS = 3
DEFAULT_INCLUDE_IMAGES = True
DEFAULT_INCLUDE_IMAGE_DESCRIPTIONS = True
DEFAULT_TAVILY_HOST = "0.0.0.0"
DEFAULT_TAVILY_PORT = "8081"

TAVILY_BASE_URL = os.environ.get('TAVILY_BASE_URL') or "https://api.tavily.com"

# --- Helper Function for Formatting Results ---
def parse_domains_list(v) -> list[str]:
    """
    Parse domain list from various input formats.
    Supports None, list, comma-separated string, and JSON string inputs.

    Args:
        v: Input domain data (None, str, or list[str])

    Returns:
        list[str]: Cleaned and parsed list of domain strings

    Examples:
        >>> parse_domains_list("example.com,test.org") -> ["example.com", "test.org"]
        >>> parse_domains_list('["example.com"]') -> ["example.com"]
        >>> parse_domains_list(None) -> []
    """
    if v is None:
        return []
    if isinstance(v, list):
        # 如果是列表，清理每个元素并去除空字符串
        return [domain.strip() for domain in v if isinstance(domain, str) and domain.strip()]
    if isinstance(v, str):
        v = v.strip()
        if not v:  # 如果是空字符串
            return []
        try:
            # 尝试解析为 JSON 列表或单个字符串
            parsed = json.loads(v)
            if isinstance(parsed, list):
                # 如果 JSON 解析结果是列表，清理每个元素
                return [domain.strip() for domain in parsed if isinstance(domain, str) and domain.strip()]
            # 如果 JSON 解析结果是单个字符串 (例如 "[\"example.com\"]" -> "example.com")
            return [parsed.strip()] if isinstance(parsed, str) and parsed.strip() else []
        except json.JSONDecodeError:
            # 如果不是有效的 JSON，尝试按逗号分隔
            if ',' in v:
                return [domain.strip() for domain in v.split(',') if domain.strip()]
            # 如果没有逗号，就认为是单个域名
            return [v]

    # 如果输入类型不匹配 (例如非字符串非列表)，返回空列表
    return []


def query_worker(i: int, query: str, search_depth: Literal["basic", "advanced"], include_domains: list[str] | None,
                 exclude_domains: list[str] | None):
    """
    Worker function to perform a single Tavily search query with retry logic.

    Args:
        i: Index identifier for the query
        query: Search query string
        search_depth: Search depth level ("basic" or "advanced")
        include_domains: Domains to specifically include
        exclude_domains: Domains to specifically exclude

    Returns:
        dict: Search results from Tavily API

    Raises:
        McpError: If search fails after retries or API returns error
    """
    request = {
        "query": query,
        "search_depth": search_depth,
        "topic": "general",
        "days": 3,
        "max_results": DEFAULT_MAX_RESULTS,
        "include_answer": True,
        "include_raw_content": False,
        "include_domains": include_domains,
        "exclude_domains": (exclude_domains or []) + DEFAULT_EXCLUDE_DOMAINS,
        "include_images": DEFAULT_INCLUDE_IMAGES,
        "include_image_descriptions": DEFAULT_INCLUDE_IMAGE_DESCRIPTIONS,
        "chunks_per_source": 3
    }
    headers = {
        "Authorization": f"Bearer {os.environ.get('TAVILY_API_KEY')}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko"
    }
    for retry in range(3):
        logging.info(f"Start search-{i} (retry={retry}) for query: '{query}'")
        try:
            delay = random.uniform(0.1, 0.9)
            sleep(delay)
            response = requests.post(
                f"{TAVILY_BASE_URL}/search", json=request, headers=headers, timeout=20, verify=False
            )
            logging.info(f"End search-{i} (retry={retry})  for query: '{query}'")

            if response.status_code == 429:
                raise McpError(ErrorData(code=INTERNAL_ERROR,
                                         message=f"Tavily API Error: Too many requests. Exceed usage limit."))
            if response.status_code == 401:
                raise McpError(ErrorData(code=INTERNAL_ERROR,
                                         message=f"Tavily API Error: {response.content}"))
            response_dict = response.json()
            if include_domains:
                response_dict["included_domains"] = include_domains
            if exclude_domains:
                response_dict["excluded_domains"] = exclude_domains

            yield
            return response_dict
        except requests.exceptions.RequestException as e:
            logging.warning(
                f"search-{i} for query: '{query}' timeout for {retry + 1} times, error is {e}", exc_info=True)
    logging.error(f"search-{i} for query: '{query}' failed")
    raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Tavily search tool timeout for query: '{query}'"))


mcp = FastMCP(
    name="mcp-tavily",
    host=os.environ.get("TAVILY_HOST", DEFAULT_TAVILY_HOST),
    port=int(os.environ.get("TAVILY_PORT", DEFAULT_TAVILY_PORT))
)


@mcp.tool()
async def tavily_batch_web_search(
        queries: Annotated[
            list[str], Field(description="need query keywords or sentences")
        ],
        search_depth: Annotated[
            Literal["basic", "advanced"],
            Field(
                default=DEFAULT_SEARCH_DEPTH,
                description="Depth of search - 'basic' or 'advanced'",
            ),
        ],
        include_domains: Annotated[
            list[str] | None,
            Field(
                default=None,
                description="List of domains to specifically include in the search results (e.g. ['example.com', 'test.org'] or 'example.com')",
            ),
        ],
        exclude_domains: Annotated[
            list[str] | None,
            Field(
                default=None,
                description="List of domains to specifically exclude from the search results (e.g. ['example.com', 'test.org'] or 'example.com')",
            ),
        ]
):
    """
    Performs a comprehensive batch web search using Tavily's AI-powered search engine.
    Excels at extracting and summarizing relevant content from web pages, making it ideal for research,
    fact-finding, and gathering detailed information.
    Returns multiple search results with AI-extracted relevant content.

    Args:
        queries(list[str]): need query keywords or sentences
        search_depth(Literal["basic", "advanced"]): Depth of search - 'basic' or 'advanced'
        include_domains(list[str]): List of domains to specifically include in the search results (e.g. ['example.com', 'test.org'] or 'example.com')
        exclude_domains(list[str]): List of domains to specifically exclude from the search results (e.g. ['example.com', 'test.org'] or 'example.com')

    Returns:
        str: result
    """
    result = []
    parsed_include_domains = parse_domains_list(include_domains)
    parsed_exclude_domains = parse_domains_list(exclude_domains)
    fixed_params = (search_depth, parsed_include_domains, parsed_exclude_domains)
    params = [(idx, query, *fixed_params) for idx, query in enumerate(queries)]
    gen = Executor("tool_call")(query_worker, params)
    try:
        while True:
            each = next(gen)
    except StopIteration as e:
        all_response = e.value

    for i, each in enumerate(all_response):
        result.append(
            each
        )
    res = json.dumps(result, ensure_ascii=False)

    logging.info(f"tavily_batch_web_search result for queries {queries}\n{res}")
    return res


if __name__ == "__main__":
    # 确保必需的动态Token相关的环境变量已设置
    check_env_vars(['TAVILY_API_KEY'])
    parser = argparse.ArgumentParser(description="Tavily mcp server")
    parser.add_argument(
        "--mode",
        type=str,
        default="streamable-http",  # 默认模式
        choices=["streamable-http", "stdio", "sse"],  # 可选模式
        help="Tavily mcp server",
    )
    args = parser.parse_args()

    mcp.run(transport=args.mode)
