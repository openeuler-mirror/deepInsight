import json
import os
from typing import List, Optional, TypeVar

from langchain_mcp_adapters.client import MultiServerMCPClient
from fastmcp.exceptions import (
    FastMCPError,
    ResourceError,
    NotFoundError
)


_T = TypeVar("_T")


class MCPClientUtils:
    """
    FastMCP配置处理器，所有方法都已改为静态方法，
    因此该类不持有任何实例状态，每次方法调用都是独立的。
    """

    @staticmethod
    def _load_config(config_file):
        """
        静态方法：加载并解析配置文件。
        不再依赖于类或实例状态，直接返回配置字典。
        """
        if not os.path.isfile(config_file):
            # Use NotFoundError if config file does not exist
            raise NotFoundError(f"MCP配置文件不存在: {config_file}")

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except json.JSONDecodeError as e:
            # Configuration file parsing error is a ResourceError
            raise ResourceError(f"配置文件格式错误: {str(e)}")
        except Exception as e:
            # Other loading errors use the base FastMCPError
            raise FastMCPError(f"加载配置文件失败: {str(e)}")

    @classmethod
    async def get_tools(cls, tools_name_list: Optional[List[str]] = None, config_file="./mcp_client_config.json",
                        server_name: Optional[str] = None):
        """
        静态方法：获取指定工具的配置信息。
        通过内部调用 _load_config 方法获取配置，不依赖任何实例状态。
        """
        # Load config directly using the static method
        config = MCPClientUtils._load_config(config_file)
        client = MultiServerMCPClient(config)
        all_tools = await client.get_tools(server_name=server_name)
        if tools_name_list:
            specified_tools = [tool for tool in all_tools if tool.name in tools_name_list]
            return specified_tools
        return all_tools
