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
from typing import Generator
from unittest.mock import patch

import httpx
from camel.models import ModelFactory
from camel.types import ModelPlatformType, ModelType
from openai import OpenAI, Stream
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

from deepinsight.core.agent.stream_chat_agent import StreamChatAgent


class TestStreamChatAgent(unittest.TestCase):
    """Unit tests for StreamChatAgent class."""
    def setUp(self):
        """Test setup that runs before each test method."""
        self.patcher1 = patch('camel.models.openai_model.OpenAIModel.token_counter')
        self.mock_token_counter = self.patcher1.start()

        def side_effect(text):
            return len(text.split())
        self.mock_token_counter.side_effect = side_effect

        os.environ["OPENAI_API_KEY"] = "sk-test"

        self.model_config = {
            "stream": True,
        }

    def tearDown(self):
        """Test cleanup that runs after each test method."""
        self.patcher1.stop()
        del os.environ["OPENAI_API_KEY"]

    def test_stream_chat_agent_with_mock_response(self):
        """Test StreamChatAgent with mocked streaming responses."""
        with patch("camel.models.model_manager.ModelManager.run") as mock_model_run:
            # Setup mock stream response
            def mock_stream_response() -> Generator[ChatCompletionChunk, None, None]:
                chunks = [
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            Choice(
                                index=0,
                                delta=ChoiceDelta(content="a")
                            )
                        ],
                        created=123,
                        model="gpt-4",
                        object="chat.completion.chunk"
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            Choice(
                                index=0,
                                delta=ChoiceDelta(content="b")
                            )
                        ],
                        created=123,
                        model="gpt-4",
                        object="chat.completion.chunk"
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            Choice(
                                index=0,
                                delta=ChoiceDelta(content="c")
                            )
                        ],
                        created=123,
                        model="gpt-4",
                        object="chat.completion.chunk",
                        usage=CompletionUsage(
                            completion_tokens=1,
                            prompt_tokens=1,
                            total_tokens=2
                        )
                    ),
                ]

                def generator():
                    for chunk in chunks:
                        yield chunk

                stream = Stream(
                    cast_to=None,
                    response=httpx.Response(status_code=200),
                    client=OpenAI(),
                )
                stream._iterator = generator()
                return stream

            mock_model_run.return_value = mock_stream_response()

            # Create StreamChatAgent instance
            stream_chat_agent = StreamChatAgent(
                system_message="",
                model=ModelFactory.create(
                    model_platform=ModelPlatformType.DEFAULT,
                    model_type=ModelType.DEFAULT,
                    model_config_dict=self.model_config
                )
            )

            # Test stream_step method
            generator = stream_chat_agent.stream_step("test input")
            content_result = ""
            try:
                while True:
                    item = next(generator)
                    if hasattr(item, "payload"):
                        content_result += item.payload
            except StopIteration as e:
                response = e.value
            self.assertEqual(content_result, "abc")
            self.assertEqual(response.info["usage"], dict(
                completion_tokens=1,
                prompt_tokens=1,
                total_tokens=2
            ))

            # Verify model run was called with correct parameters
            mock_model_run.assert_called_once()
