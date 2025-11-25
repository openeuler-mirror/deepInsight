import re
from typing import Annotated, ClassVar, Optional

from langchain_core.documents import Document
from pydantic import BaseModel, Field, ConfigDict


class BaseLoader(BaseModel):
    DEFAULT_IMAGE_REGEX: ClassVar[re.Pattern[str]] = re.compile(r"""!\[(.*?)\]\((images/[a-zA-Z0-9]+\.(jpg|png))\)""")
    image_regex: re.Pattern[str] = DEFAULT_IMAGE_REGEX
    """
    A patten to match alt text and image file name.
    Should keep the first group be alt text and the second one be filename with 'images/' prefix.
    """

    async def batch_process(self, files: dict[str, bytes]) -> "dict[str, ParseResult | Exception]":
        return {name: await self.process(name, content) for name, content in files.items()}

    async def process(self, name: str, content: bytes) -> "ParseResult":
        raise NotImplementedError(f"Loader {type(self).__name__} doesn't implements process method.")



class ParseResult(BaseModel):
    """A dataclass to allow a parser to output content with new images.

    Examples:
        >>> from langchain_core.documents import Document
        >>> ParseResult(
        ...     text=[Document(page_content="An example of image: ![](images/a.png)")],
        ...     images={"a.png": b"..."},
        ... )
    """
    model_config = ConfigDict(extra="forbid")

    text: list[Document]
    """If including new images in `self.images`, it should be a Markdown image `![](images/{key_in_images})`."""
    images: Annotated[dict[str, bytes], Field(default_factory=dict)]
    """Image mapping for text."""
    image_regex: Optional[re.Pattern[str]] = BaseLoader.DEFAULT_IMAGE_REGEX
    """See `BaseLoader.image_regex`. What's more, if a replacement is applied to the images, it changes to `None`."""

    DEFAULT_IMAGE_REGEX: ClassVar[re.Pattern[str]] = BaseLoader.DEFAULT_IMAGE_REGEX
