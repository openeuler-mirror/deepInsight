from typing import Callable, Iterable

from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import MessageLikeRepresentation
from langchain_core.messages.utils import count_tokens_approximately

_DEFAULT_MESSAGES_TO_KEEP = 20
_DEFAULT_TRIM_TOKEN_LIMIT = 4000
_DEFAULT_FALLBACK_MESSAGE_COUNT = 15
_SEARCH_RANGE_FOR_TOOL_PAIRS = 5
TokenCounter = Callable[[Iterable[MessageLikeRepresentation]], int]


class DefaultSummarizationMiddleware(SummarizationMiddleware):
    """Inherits from SummarizationMiddleware and provides a default value for max_tokens_before_summary."""

    def __init__(
            self,
            model: str | BaseChatModel,
            max_tokens_before_summary: int | None = 8000,  # Set a default value for max_tokens_before_summary
            messages_to_keep: int = _DEFAULT_MESSAGES_TO_KEEP,
            token_counter: TokenCounter = count_tokens_approximately,
            summary_prompt: str = DEFAULT_SUMMARY_PROMPT,
    ) -> None:
        """Initialize the middleware, providing a default for max_tokens_before_summary."""
        # Call the parent class's __init__ method with the provided or default values.
        super().__init__(
            model=model,
            max_tokens_before_summary=max_tokens_before_summary,
            messages_to_keep=messages_to_keep,
            token_counter=token_counter,
            summary_prompt=summary_prompt,
        )
