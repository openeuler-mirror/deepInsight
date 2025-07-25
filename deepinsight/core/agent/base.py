# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from __future__ import annotations

from abc import abstractmethod, ABC
from typing import Any, Dict, Optional, TypeVar, Generator, Generic

from camel.agents import ChatAgent
from camel.responses import ChatAgentResponse
from camel.toolkits import MCPToolkit

from deepinsight.config.model import ModelConfig
from deepinsight.core.agent.stream_chat_agent import StreamChatAgent
from deepinsight.core.types.messages import Message
from deepinsight.utils.aio import get_or_create_loop

OutputType = TypeVar("OutputType")

class BaseAgent(ABC, Generic[OutputType]):
    """
    Minimalist BaseAgent class that handles four core responsibilities:
    1. Building system prompts
    2. Building user prompts
    3. Connecting to MCP Server
    4. Parsing LLM outputs

    This serves as the foundation for specialized agent implementations.
    """

    def __init__(
            self,
            model_config: ModelConfig,
            mcp_tools_config_path: Optional[str] = None,
            mcp_client_timeout: Optional[int] = None,
    ) -> None:
        """
        Initialize the base agent with configuration.

        Args:
            model_config: Configuration for the AI model
            mcp_tools_config_path: Path to MCP tools configuration file
            mcp_client_timeout: Timeout for MCP client operations
        """
        self.mcp_toolkit_instance = None
        self.mcp_tools_config_path = mcp_tools_config_path
        self.mcp_client_timeout = mcp_client_timeout

        self.connect_mcp()
        if model_config.model_config_dict and model_config.model_config_dict.get("stream", False):
            self.agent = StreamChatAgent(
                system_message=self.build_system_prompt(),
                model=model_config.to_model_backend(),
                tools=self.mcp_toolkit_instance.get_tools() if self.mcp_toolkit_instance else None,
            )
        else:
            self.agent = ChatAgent(
                    system_message=self.build_system_prompt(),
                    model=model_config.to_model_backend(),
                    tools=self.mcp_toolkit_instance.get_tools() if self.mcp_toolkit_instance else None,
                )

    @abstractmethod
    def build_system_prompt(self) -> str:
        """
        Abstract method to construct the system-level prompt.

        Should be implemented by subclasses to define:
        - Agent role and capabilities
        - Response format requirements
        - Behavioral guidelines

        Returns:
            str: The complete system prompt
        """
        ...

    @abstractmethod
    def build_user_prompt(
            self,
            *,
            query: str,
            context: Dict[str, Any] | None = None,
    ) -> str:
        """
        Abstract method to construct the user-level prompt.

        Should be implemented by subclasses to properly format:
        - The user query
        - Any additional context
        - Task-specific instructions

        Args:
            query: The user's input query
            context: Optional additional context dictionary

        Returns:
            str: The complete user prompt
        """
        ...

    def connect_mcp(self) -> None:
        """
        Establish connection to MCP Server and initialize toolkit.

        Uses the configured path and timeout values.
        Runs synchronously using the event loop.
        """
        if self.mcp_tools_config_path:
            # Initialize MCP toolkit
            loop = get_or_create_loop()
            self.mcp_toolkit_instance = MCPToolkit(
                config_path=str(self.mcp_tools_config_path),
                timeout=self.mcp_client_timeout
            )
            loop.run_until_complete(self.mcp_toolkit_instance.connect())

    def parse_output(self, response: ChatAgentResponse) -> OutputType:
        """
        Parse and transform the raw LLM response.

        Base implementation returns the response as-is.
        Subclasses should override to implement:
        - Response validation
        - Data extraction
        - Format transformation

        Args:
            response: The raw chat agent response

        Returns:
            T: The parsed output (type determined by subclass)
        """
        return response

    def run(
            self,
            query: str,
            context: Dict[str, Any] | None = None,
    ) -> Generator[Message, None, OutputType]:
        """
        Execute the full agent workflow.

        1. Builds the user prompt
        2. Executes via appropriate agent type (streaming/non-streaming)
        3. Parses and returns the output

        Args:
            query: The input query to process
            context: Optional additional context

        Yields:
            Message: Streaming messages (if using streaming agent)

        Returns:
            T: The parsed output from parse_output()
        """
        prompt = self.build_user_prompt(query=query, context=context)
        if isinstance(self.agent, StreamChatAgent):
            response = yield from self.agent.stream_step(prompt)
        else:
            response = self.agent.step(prompt)
        output = self.parse_output(response)
        self.post_run(output)
        return output

    def post_run(self, output: OutputType) -> None:
        """
        Post-processing hook that is called after run() completes successfully.

        This method does not modify the output, but allows subclasses to perform
        additional operations after the main execution is done.

        Args:
            output: The output from run() method
        """
        pass