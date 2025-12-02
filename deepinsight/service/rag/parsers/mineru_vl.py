from __future__ import annotations

import asyncio
import base64
import logging
import re
import os
import urllib.parse

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.documents import Document as LCDocument

from deepinsight.config.rag_config import MineruParserConfig
from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.service.rag.parsers.base import BaseDocumentParser
from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocumentPayload
from deepinsight.utils.file_storage import get_storage_impl
from deepinsight.utils.file_storage.identify import KbDocImage


class MineruVLParser(BaseDocumentParser):
    """Parser that keeps the legacy MinerU + VL pipeline."""

    def __init__(self, config: MineruParserConfig):
        self._config = config

    async def parse(self, payload: DocumentPayload, kb_id: int | str, resource_prefix: str) -> LoaderOutput:
        parse_result = await _parse_file_content(payload.filename, payload.binary_content)

        if parse_result.images:
            await get_storage_impl().object_init_bucket(KbDocImage(kb_id=kb_id), exist_ok=True)
            img_map_with_path = {f"images/{k}": v for k, v in parse_result.images.items()}
            directory = KbDocImage(kb_id=kb_id, doc_id=payload.doc_id)
            await get_storage_impl().object_put(directory, img_map_with_path)
            dir_uri = directory.uri_postfix()
            if not dir_uri.endswith("/"):
                dir_uri += "/"
            path_map = {
                k: dir_uri + k for k in img_map_with_path
            }
            await _replace_image_link(
                parse_result, path_map=path_map,
                replace_alt_text=bool(self._config.enable_vl if self._config else True),
                prefix=resource_prefix,
            )

        return LoaderOutput(result=parse_result, file_paths=[payload.filename])


async def _parse_file_content(filename: str, binary: bytes) -> ParseResult:
    """Parse file content using MinerU for office documents or LangChain loaders for other types."""
    ext = filename.lower().rsplit(".")[-1]
    if ext in {"pdf", "docx", "doc", "pptx", "ppt"}:
        try:
            from deepinsight.service.rag.loaders.mineru_online import MinerUOnlineClient
            return await MinerUOnlineClient().process(filename, binary)
        except Exception as e:
            logging.error("Failed to parse %r using MinerU: %s", filename, e)
            raise
    text = _extract_text(ext, binary)
    return ParseResult(text=[LCDocument(page_content=text, metadata={"source": filename})])


def _extract_text(ext: str, binary: bytes) -> str:
    try:
        return binary.decode("utf8")
    except UnicodeDecodeError:
        pass
    if ext in {"txt", "md", "markdown"}:
        try:
            return binary.decode("gb2312")
        except Exception:  # noqa: fallback
            pass
        return binary.decode("utf8", errors="ignore")
    from deepinsight.service.conference.paper_extractor import PaperParseException
    raise PaperParseException("Unsupported file. Please select another parser to parse this file.")


async def _replace_image_link(doc: ParseResult, replace_alt_text: bool = True, path_map: dict[str, str] = None,
                              vl: BaseChatModel = None, prefix: str = "") -> ParseResult:
    if not doc.images:
        return doc
    path_map = path_map or {}
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    used_images: dict[str, bytes] = {}
    for chunk in doc.text:
        for match in doc.image_regex.finditer(chunk.page_content):
            img_path = match.group(2)
            if not img_path.startswith("images/"):
                continue
            img_path = img_path[7:]  # remove 'images/' prefix
            if img_path in doc.images:
                used_images[img_path] = doc.images[img_path]

    if not used_images:
        return doc

    if replace_alt_text:
        replacement = await _create_image_description_batch(used_images, vl)
        replacement = {f"images/{k}": v for k, v in replacement.items() if v}
        if not replacement:
            logging.warning(f"All failure on creating image description for {len(used_images)} images.")
        else:
            logging.info("Try create %s image description and %s succeeded.", len(used_images), len(replacement))
    else:
        replacement = {}

    def replace_func(m: re.Match[str]) -> str:
        url = m.group(2)
        if not url.startswith("images/"):
            return m.group()

        new_alt = replacement[url] if url in replacement else m.group(1)
        new_path = f"{prefix}{urllib.parse.quote(path_map.get(url, url), safe='/')}"
        return f"![{new_alt}]({new_path})"

    for chunk in doc.text:
        chunk.page_content = doc.image_regex.sub(replace_func, chunk.page_content)
    doc.image_regex = None
    return doc


_CREATE_IMAGE_DESC_PROMPT = r"""根据输入的图片内容，按照以下规则生成json格式的响应：

1. 如果图片是表格：
{
  "type": "table",
  "title": "总结出一个表格的标题",
  "content": "表格内容的Markdown文本（包含表头与行列数据）"
}

2. 如果图片是其他类型：
{
  "type": "picture",
  "title": "总结出图片的标题",
  "text": "图片中的文字内容（若无文字则返回空字符串）",
  "content": "对图片内容的客观描述"
}

要求：

- 使用中文进行描述
- 对于内容为表格的图片，需转换为标准Markdown格式
- 图片描述需要简洁客观，避免主观判断
- 不要解释或添加额外说明，直接返回JSON字符串
"""


async def _create_image_description_batch(images: dict[str, bytes], vl: BaseChatModel = None) -> dict[str, str | None]:
    if vl is None:
        logging.info("Create image description without a given VL model. Using default model.")
        try:
            vl = _create_image_desc_default_model()
        except Exception as e:
            logging.warning(
                "Try create image description but %s raised when create VL models. Skip: %s",
                type(e).__name__,
                e,
            )
            return {}
    results = await asyncio.gather(
        *[_create_image_description(name, content, vl=vl) for name, content in images.items()]
    )
    return dict(results)


async def _create_image_description(filename: str, image: bytes, vl: BaseChatModel) -> tuple[str, str | None]:
    file_format = filename.rsplit(".", 1)[-1]
    url = dict(
        url=f"data:image/{file_format};base64," + base64.b64encode(image).decode("utf8"),
        detail="high",
    )
    inputs = [
        HumanMessage(content=[dict(type="image_url", image_url=url), dict(type="text", text=_CREATE_IMAGE_DESC_PROMPT)])
    ]
    try:
        response = (await vl.ainvoke(inputs)).content
        if isinstance(response, str):
            return filename, response
        ret = ""
        for chunk in response:
            if isinstance(chunk, str):
                ret += chunk
            elif isinstance(chunk, dict):
                if (chunk.get("type") == "text") and isinstance(chunk.get("text"), str):
                    ret += chunk.get("text")
                else:
                    logging.debug("Drop an output chunk which is not a text chunk from VLM.")
            else:
                raise TypeError(f"Unexpected return type from VLM. VLM returns {response}.")
        return filename, ret
    except Exception as e:
        logging.error("Create Image description failed with %s: %s", type(e).__name__, e, exc_info=True)
        return filename, None


def _create_image_desc_default_model() -> BaseChatModel:
    url = os.environ.get("RAG_VL_URL")
    key = os.environ.get("RAG_VL_API_KEY")
    model = os.environ.get("RAG_VL_MODEL")

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(openai_api_base=url, openai_api_key=key, model=model)  # type: ignore
