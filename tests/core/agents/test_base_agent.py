# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import os
import unittest
import uuid
from unittest.mock import patch, MagicMock
from typing import Generator, Dict, Any
from deepinsight.core.agent.base import BaseAgent
from deepinsight.config.model import ModelConfig
from camel.agents import ChatAgent
from camel.responses import ChatAgentResponse
from deepinsight.core.agent.stream_chat_agent import StreamChatAgent
from deepinsight.core.types.messages import ChunkMessage
from camel.types import ModelPlatformType, ModelType


class MockAgent(BaseAgent):
    """Mock implementation of BaseAgent for testing purposes.

    This class implements abstract methods from BaseAgent with simple mock behavior.
    """

    def build_system_prompt(self) -> str:
        return "System prompt"

    def build_user_prompt(self, *, query: str, context: Dict[str, Any] | None = None) -> str:
        return f"User prompt: {query}"

    def parse_output(self, response: ChatAgentResponse) -> ChatAgentResponse:
        return response


class TestBaseAgent(unittest.TestCase):
    """Test suite for BaseAgent functionality."""

    def setUp(self):
        """Test setup that runs before each test method."""
        self.mock_token_counter = patch('camel.models.openai_model.OpenAIModel.token_counter').start()
        def side_effect(text):
            return len(text.split())
        self.mock_token_counter.side_effect = side_effect

        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self):
        """Test cleanup that runs after each test method."""
        del os.environ["OPENAI_API_KEY"]
        patch.stopall()

    def _get_model_config(self, stream: bool) -> ModelConfig:
        """Helper method to create a ModelConfig with specified streaming setting.

         Args:
             stream: Boolean indicating whether streaming should be enabled

         Returns:
             Configured ModelConfig instance
         """
        return ModelConfig(
            model_platform=ModelPlatformType.DEFAULT,
            model_type=ModelType.DEFAULT,
            model_config_dict={"stream": stream},
            api_key="test_key"
        )

    def test_streaming_agent_initialization(self):
        """Test that agent initializes with StreamChatAgent when streaming is enabled."""
        config = self._get_model_config(stream=True)
        agent = MockAgent(
            model_config=config,
            mcp_client_timeout=30
        )

        self.assertIsInstance(agent.agent, StreamChatAgent)

    def test_non_streaming_agent_initialization(self):
        """Test that agent initializes with regular ChatAgent when streaming is disabled."""
        config = self._get_model_config(stream=False)
        agent = MockAgent(
            model_config=config,
            mcp_client_timeout=30
        )

        self.assertIsInstance(agent.agent, ChatAgent)
        self.assertNotIsInstance(agent.agent, StreamChatAgent)

    def test_run_method_streaming(self):
        """Test the run() method in streaming mode produces expected chunks."""
        config = self._get_model_config(stream=True)
        agent = MockAgent(model_config=config)

        # Mock stream_step behavior
        mock_response = MagicMock(spec=ChatAgentResponse)
        mock_response.output_messages = ["Test response"]

        def mock_stream_step(prompt):
            yield ChunkMessage(payload="Stream chunk 1", stream_id=str(uuid.uuid4()))
            yield ChunkMessage(payload="Stream chunk 2", stream_id=str(uuid.uuid4()))
            return mock_response

        with patch.object(agent.agent, 'stream_step', new=mock_stream_step):
            result = agent.run("test query")

            # Verify streaming behavior
            self.assertIsInstance(result, Generator)
            chunks = list(result)
            self.assertEqual(len(chunks), 2)
            self.assertEqual(chunks[0].payload, "Stream chunk 1")
            self.assertEqual(chunks[1].payload, "Stream chunk 2")

    def test_run_method_non_streaming(self):
        """Test the run() method in non-streaming mode produces expected response."""
        config = self._get_model_config(stream=False)
        agent = MockAgent(model_config=config)

        # Mock step behavior
        mock_response = MagicMock(spec=ChatAgentResponse)
        mock_response.output_messages = ["Test response"]

        with patch.object(agent.agent, 'step', return_value=mock_response) as mock_step:
            result = agent.run("test query")
            # Verify streaming behavior
            self.assertIsInstance(result, Generator)
            try:
                while True:
                    item = next(result)
            except StopIteration as e:
                result = e.value
            # Verify non-streaming behavior
            mock_step.assert_called_once_with("User prompt: test query")
            self.assertEqual(result, mock_response)
