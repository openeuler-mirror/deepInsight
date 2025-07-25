# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import json
import logging
import uuid
from typing import List, Generator, Union, Optional, Type, Dict, Any

from camel.agents import ChatAgent
from camel.agents._types import ToolCallRequest, ModelResponse
from camel.messages import BaseMessage, OpenAIMessage
from camel.models import BaseModelBackend, ModelProcessingError
from camel.responses import ChatAgentResponse
from camel.types import OpenAIBackendRole
from camel.types.agents import ToolCallingRecord
from openai import Stream
from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.chat.chat_completion_message_tool_call import Function
from pydantic import BaseModel

from deepinsight.core.messages import Message, CompleteMessage, StartMessage, EndMessage, ChunkMessage

logger = logging.getLogger(__name__)

class NotStreamModelException(Exception):
    """Exception raised when a non-streaming model is used with StreamChatAgent."""
    pass


class StreamChatAgent(ChatAgent):
    """
     A specialized ChatAgent that handles streaming model responses.

     Inherits from ChatAgent and adds streaming-specific functionality including:
     - Validation of model streaming capabilities
     - Streaming response handling
     - Real-time message generation
     """

    def __init__(self, *args, **kwargs):
        """
        Initialize the streaming chat agent.

        Args:
            *args: Positional arguments for parent class
            **kwargs: Keyword arguments for parent class

        Raises:
            NotStreamModelException: If any model doesn't support streaming
            TypeError: If model stream attribute has incorrect type
        """
        super().__init__(*args, **kwargs)
        # Check whether it is a streaming model call
        self._validate_streaming_models(self.model_backend.models)

    def _validate_streaming_models(self, models: List[BaseModelBackend]) -> None:
        """
        Validate that all models support streaming.

        Args:
            models: List of model backends to validate

        Raises:
            TypeError: If stream attribute is not boolean
            NotStreamModelException: If any model doesn't support streaming
        """
        invalid_models = []
        type_mismatch = []

        for model in models:
            if not isinstance(model.stream, bool):
                type_mismatch.append(
                    f"Model '{model.model_type}': expected bool, got {type(model.stream)}"
                )
            elif not model.stream:
                invalid_models.append(model.model_type)

        if type_mismatch:
            raise TypeError(
                "Invalid stream attribute types:\n"
                + "\n".join(type_mismatch)
            )

        if invalid_models:
            raise NotStreamModelException(
                "These models don't config streaming:\n"
                f"{', '.join(invalid_models)}\n\n"
                "Required action:\n"
                "1. Set stream=True in model config dict\n"
                "2. Or use non-streaming agent class ChatAgent"
            )

    def stream_step(
            self,
            input_message: Union[BaseMessage, str],
            response_format: Optional[Type[BaseModel]] = None,
    ) -> Generator[Message, None, ChatAgentResponse]:
        """
        Execute a streaming step with the agent.

        Args:
            input_message: Input message or string to process
            response_format: Optional Pydantic model for response formatting

        Yields:
            Message: Streaming messages during processing

        Returns:
            ChatAgentResponse: Final response after completion

        Note:
            Handles Langfuse session tracking if available
            Manages response formatting with non-strict tools
            Processes tool calls and external tool requests
        """
        # Set Langfuse session_id using agent_id for trace grouping
        try:
            from camel.utils.langfuse import set_current_agent_session_id

            set_current_agent_session_id(self.agent_id)
        except ImportError:
            pass  # Langfuse not available

        # Handle response format compatibility with non-strict tools
        original_response_format = response_format
        input_message, response_format, used_prompt_formatting = (
            self._handle_response_format_with_non_strict_tools(
                input_message, response_format
            )
        )

        # Convert input message to BaseMessage if necessary
        if isinstance(input_message, str):
            input_message = BaseMessage.make_user_message(
                role_name="User", content=input_message
            )

        # Add user input to memory
        self.update_memory(input_message, OpenAIBackendRole.USER)

        tool_call_records: List[ToolCallingRecord] = []
        external_tool_call_requests: Optional[List[ToolCallRequest]] = None

        accumulated_context_tokens = (
            0  # This tracks cumulative context tokens, not API usage tokens
        )

        # Initialize token usage tracker
        step_token_usage = self._create_token_usage_tracker()
        iteration_count = 0

        while True:
            try:
                openai_messages, num_tokens = self.memory.get_context()
                accumulated_context_tokens += num_tokens
            except RuntimeError as e:
                return self._step_terminate(
                    e.args[1], tool_call_records, "max_tokens_exceeded"
                )
            # Get response from model backend
            response = yield from self._stream_get_model_response(
                openai_messages,
                accumulated_context_tokens,  # Cumulative context tokens
                response_format,
                self._get_full_tool_schemas(),
            )
            iteration_count += 1

            # Accumulate API token usage
            self._update_token_usage_tracker(
                step_token_usage, response.usage_dict
            )

            # Terminate Agent if stop_event is set
            if self.stop_event and self.stop_event.is_set():
                # Use the _step_terminate to terminate the agent with reason
                return self._step_terminate(
                    accumulated_context_tokens,
                    tool_call_records,
                    "termination_triggered",
                )

            if tool_call_requests := response.tool_call_requests:
                # Process all tool calls
                for tool_call_request in tool_call_requests:
                    if (
                            tool_call_request.tool_name
                            in self._external_tool_schemas
                    ):
                        if external_tool_call_requests is None:
                            external_tool_call_requests = []
                        external_tool_call_requests.append(tool_call_request)
                    else:
                        tool_call_record = self._execute_tool(tool_call_request)
                        tool_call_records.append(
                            tool_call_record
                        )
                        yield CompleteMessage[ToolCallingRecord](
                            payload=tool_call_record, stream_id=str(uuid.uuid4())
                        )

                # If we found external tool calls, break the loop
                if external_tool_call_requests:
                    break

                if (
                        self.max_iteration is not None
                        and iteration_count >= self.max_iteration
                ):
                    break

                # If we're still here, continue the loop
                continue

            break

        self._format_response_if_needed(response, response_format)

        # Apply manual parsing if we used prompt-based formatting
        if used_prompt_formatting and original_response_format:
            self._apply_prompt_based_parsing(
                response, original_response_format
            )

        self._record_final_output(response.output_messages)

        return self._convert_to_chatagent_response(
            response,
            tool_call_records,
            accumulated_context_tokens,
            external_tool_call_requests,
            step_token_usage["prompt_tokens"],
            step_token_usage["completion_tokens"],
            step_token_usage["total_tokens"],
        )

    def _stream_get_model_response(
            self,
            openai_messages: List[OpenAIMessage],
            num_tokens: int,
            response_format: Optional[Type[BaseModel]] = None,
            tool_schemas: Optional[List[Dict[str, Any]]] = None,
    ) -> Generator[Message, None, ModelResponse]:
        """
        Get model response in streaming mode.

        Args:
            openai_messages: List of messages in OpenAI format
            num_tokens: Number of tokens in prompt
            response_format: Optional response format model
            tool_schemas: List of tool schemas

        Yields:
            Message: Streaming messages

        Returns:
            ModelResponse: Parsed model response

        Raises:
            ModelProcessingError: If model processing fails
        """
        response = None
        try:
            response = self.model_backend.run(
                openai_messages, response_format, tool_schemas or None
            )
        except Exception as exc:
            logger.error(
                f"An error occurred while running model "
                f"{self.model_backend.model_type}, "
                f"index: {self.model_backend.current_model_index}",
                exc_info=exc,
            )
            error_info = str(exc)

        if not response and self.model_backend.num_models > 1:
            raise ModelProcessingError(
                "Unable to process messages: none of the provided models "
                "run successfully."
            )
        elif not response:
            raise ModelProcessingError(
                f"Unable to process messages: the only provided model "
                f"did not run successfully. Error: {error_info}"
            )

        if isinstance(response, ChatCompletion):
            raise ModelProcessingError(
                f"Received not stream model response, if you want to use not stream model, "
                f"please use non-streaming agent class ChatAgent."
            )
        parsed_response = yield from self._handle_stream_response(response, num_tokens)
        return parsed_response

    def _handle_stream_response(
            self,
            response: Stream[ChatCompletionChunk],
            prompt_tokens: int,
    ) -> Generator[Message, None, ModelResponse]:
        """
        Handle streaming response from model.

        Args:
            response: Stream of chat completion chunks
            prompt_tokens: Number of tokens in prompt

        Yields:
            Message: Streaming messages (Start, Chunk, End)

        Returns:
            ModelResponse: Processed model response
        """
        content_dict: Dict[int, str] = {}
        finish_reasons_dict: Dict[int, str] = {}
        output_messages: List[BaseMessage] = []
        usage_dict: Optional[Dict[str, Any]] = None
        tool_call_request_dict: Dict[int, List[ChatCompletionMessageToolCall]] = {}
        response_id: str = ""

        stream_id = str(uuid.uuid4())
        first_stream = True
        # All choices in one response share one role
        for chunk in response:
            # Some model platforms like siliconflow may return None for the
            # chunk.id
            response_id = chunk.id if chunk.id else str(uuid.uuid4())
            if chunk.usage:
                usage_dict = dict(
                    completion_tokens=chunk.usage.completion_tokens,
                    prompt_tokens=chunk.usage.prompt_tokens,
                    total_tokens=chunk.usage.total_tokens,
                )
            for choice in chunk.choices:
                index = choice.index
                delta = choice.delta
                self._handle_content_delta(
                    index, delta, content_dict
                )

                self._handle_finish_reasons_delta(
                    index, finish_reasons_dict, choice.finish_reason
                )

                self._handle_output_messages(
                    index, output_messages, choice.finish_reason, content_dict
                )

                self._handle_tool_call_request(
                    index, tool_call_request_dict, delta
                )
                # yield stream data
                if delta.content is not None:
                    if first_stream:
                        yield StartMessage[str](
                            stream_id=stream_id,
                            payload=delta.content
                        )
                    elif choice.finish_reason:
                        yield EndMessage[str](
                            stream_id=stream_id,
                            payload=delta.content
                        )
                    else:
                        yield ChunkMessage[str](
                            stream_id=stream_id,
                            payload=delta.content
                        )
            first_stream = False
        finish_reasons = [
            finish_reasons_dict[i] for i in range(len(finish_reasons_dict))
        ]

        if not usage_dict:
            usage_dict = self.get_usage_dict(output_messages, prompt_tokens)

        tool_call_requests = []
        for index, tool_call_request_for_index in tool_call_request_dict.items():
            for each in tool_call_request_for_index:
                try:
                    args = json.loads(each.function.arguments)
                    tool_call_request = ToolCallRequest(
                        tool_name=each.function.name, args=args, tool_call_id=each.id
                    )
                    tool_call_requests.append(tool_call_request)
                except Exception as e:
                    raise ModelProcessingError(
                        f"Model return invalid json format function call arguments for name {each.function.name}, "
                        f"arguments is {each.function.arguments}"
                    )

        return ModelResponse(
            response=response,
            tool_call_requests=tool_call_requests if tool_call_requests else None,
            output_messages=output_messages,
            finish_reasons=finish_reasons,
            usage_dict=usage_dict,
            response_id=response_id,
        )

    def _handle_content_delta(
            self,
            index: int,
            delta: ChoiceDelta,
            content_dict: Dict[int, str],
    ) -> None:
        """
        Accumulate content from stream deltas.

        Args:
            index: Choice index
            delta: Current delta chunk
            content_dict: Dictionary accumulating content by index
        """
        if delta.content is not None:
            if index not in content_dict:
                content_dict.setdefault(index, "")
            content_dict[index] += delta.content

    def _handle_finish_reasons_delta(
            self,
            index: int,
            finish_reasons_dict: Dict[int, str],
            finish_reason: str
    ) -> None:
        """
        Track finish reasons for each choice.

        Args:
            index: Choice index
            finish_reasons_dict: Dictionary tracking finish reasons
            finish_reason: Current finish reason
        """
        if finish_reason:
            if index not in finish_reasons_dict:
                finish_reasons_dict.setdefault(index, "")
            finish_reasons_dict[index] = finish_reason

    def _handle_output_messages(
            self,
            index: int,
            output_messages: List[BaseMessage],
            finish_reason: str,
            content_dict: Dict[int, str]
    ) -> None:
        """
        Create output messages when choices finish.

        Args:
            index: Choice index
            output_messages: List to accumulate finished messages
            finish_reason: Current finish reason
            content_dict: Dictionary of accumulated content
        """
        if finish_reason:
            chat_message = BaseMessage(
                role_name=self.role_name,
                role_type=self.role_type,
                meta_dict=dict(),
                content=content_dict[index],
            )
            output_messages.append(chat_message)

    def _handle_tool_call_request(
            self,
            index: int,
            tool_call_request_dict: Dict[int, List[ChatCompletionMessageToolCall]],
            delta: ChoiceDelta,
    ) -> None:
        """
         Accumulate tool call requests from stream deltas.

         Args:
             index: Choice index
             tool_call_request_dict: Dictionary accumulating tool calls
             delta: Current delta chunk
         """
        if delta.tool_calls is not None:
            if index not in tool_call_request_dict:
                tool_call_request_dict.setdefault(index, [])

            for tool_call in delta.tool_calls:
                if hasattr(tool_call, 'index'):
                    # 确保tool_calls_dict[index]足够长
                    while len(tool_call_request_dict[index]) <= tool_call.index:
                        tool_call_request_dict[index].append(ChatCompletionMessageToolCall(
                            id="",
                            type="function",
                            function=Function(
                                name="",
                                arguments="",
                            )
                        ))

                    # 更新工具调用信息
                    if tool_call.id is not None:
                        tool_call_request_dict[index][tool_call.index].id += tool_call.id
                    if tool_call.function.name is not None:
                        tool_call_request_dict[index][tool_call.index].function.name  += tool_call.function.name
                    if tool_call.function.arguments is not None:
                        tool_call_request_dict[index][tool_call.index].function.arguments += tool_call.function.arguments