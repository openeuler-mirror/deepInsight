from __future__ import annotations

import logging
import os


import asyncio
import base64
import re
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.documents import Document as LCDocument

from deepinsight.config.rag_config import MineruParserConfig
from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.service.rag.parsers.base import BaseDocumentParser
from deepinsight.service.rag.types import LoaderOutput
from deepinsight.service.schemas.rag import DocumentPayload



class MineruVLParser(BaseDocumentParser):
    """Parser that keeps the legacy MinerU + VL pipeline."""

    def __init__(self, config: MineruParserConfig):
        self._config = config

    async def parse(self, payload: DocumentPayload, working_dir: str) -> LoaderOutput:
        if not payload.source_path:
            raise ValueError("MinerU parser requires payload.source_path to be provided")
        file_path = payload.source_path
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)
        
        parse_result = await _parse_file_content(file_path)

        # Process images if present
        if getattr(parse_result, "images", None):
            doc_name = payload.source_path or (payload.metadata or {}).get("source") or payload.doc_id
            await _store_images(
                working_dir,
                payload.doc_id,
                doc_name,
                parse_result.images,
            )
            await _replace_image_link(
                parse_result,
                replace_alt_text=bool(self._config.enable_vl if self._config else True),
                prefix=os.path.join("..", "..", working_dir, payload.doc_id),
            )

        return LoaderOutput(result=parse_result, file_paths=[file_path])


async def _parse_file_content(file_path: str) -> ParseResult:
    """Parse file content using MinerU for office documents or LangChain loaders for other types."""
    ext = os.path.splitext(file_path.lower())[1]
    doc_with_resource: ParseResult | None = None
    docs = []
    try:
        if ext in {".pdf", ".docx", ".doc", ".pptx", ".ppt"}:
            try:
                from deepinsight.service.rag.loaders.mineru_online import MinerUOnlineClient

                with open(file_path, mode="rb") as f:
                    doc_with_resource = await MinerUOnlineClient().process(os.path.basename(file_path), f.read())
            except Exception as e:
                logging.error("Failed to parse %r using MinerU: %s", file_path, e)
                docs = []
        elif ext in {".txt", ".md", ".markdown"}:
            try:
                from langchain_community.document_loaders import TextLoader

                loader = TextLoader(file_path, encoding="utf-8")
                docs = loader.load()
            except Exception:
                docs = []
        elif ext == ".csv":
            try:
                from langchain_community.document_loaders import CSVLoader

                loader = CSVLoader(file_path)
                docs = loader.load()
            except Exception:
                docs = []
        else:
            try:
                from langchain_community.document_loaders import TextLoader

                loader = TextLoader(file_path, encoding="utf-8")
                docs = loader.load()
            except Exception:
                docs = []
    except Exception:
        docs = []

    if not (docs or doc_with_resource):
        logging.warning("Extraction on file %s failed, fallback to plain text reader.", file_path)
        text = _extract_text(file_path)
        return ParseResult(text=[LCDocument(page_content=text, metadata={"source": file_path})])

    return doc_with_resource or ParseResult(text=docs)


def _extract_text(file_path: str) -> str:
    _, ext = os.path.splitext(file_path.lower())
    text_based_exts = {".txt", ".md", ".markdown"}
    try:
        if ext in text_based_exts:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        try:
            import textract

            content = textract.process(file_path)
            return content.decode("utf-8", errors="ignore")
        except Exception:
            with open(file_path, "rb") as f:
                raw = f.read()
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"Text extraction failed for {file_path}: {e}") from e


async def _store_images(working_dir: str, doc_id: str, doc_name: str, images: dict[str, bytes]) -> None:
    doc_name = os.path.basename(doc_name)
    img_dir = os.path.join(working_dir, doc_id, "images")
    logging.debug("Begin to store %d images to %r for document %r", len(images), img_dir, doc_name)
    if not os.path.exists(img_dir):
        os.makedirs(img_dir, exist_ok=True)

    belong_file_path = os.path.join(img_dir, "belongs_to.txt")
    try:
        with open(belong_file_path, mode="xt+", encoding="utf8") as belong_file:
            belong_file.write(doc_name)
            existed = "an unknown document"
    except FileExistsError:
        with open(belong_file_path, mode="rt", encoding="utf8") as belong_file:
            existed = belong_file.read()
            existed = existed if len(existed) < 256 else (existed[:256] + "...")
            existed = f"document named {existed!r}"

    for filename, content in images.items():
        file_path = os.path.join(img_dir, filename)
        try:
            with open(file_path, mode="xb") as f:
                f.write(content)
                continue
        except FileExistsError:
            pass
        logging.debug("Begin to store image for %r to %r", doc_name, file_path)
        with open(file_path, mode="wb+") as f:
            if f.read() != content:
                logging.warning("Image %s already exists for %s, overwrite.", file_path, existed)
                f.write(content)
            else:
                logging.debug("Image %s already exists for %s with same content, skip.", file_path, existed)
    logging.debug("End to store %d images to %r for document %r", len(images), img_dir, doc_name)


async def _replace_image_link(doc: ParseResult, replace_alt_text: bool = True,
                              vl: BaseChatModel = None, prefix: str = "") -> ParseResult:
    if not doc.images:
        return doc
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
        replacement = {}

    def replace_func(m: re.Match[str]) -> str:
        url = m.group(2)
        if not url.startswith("images/"):
            return m.group()

        new_alt = replacement[url] if url in replacement else m.group(1)
        new_path = f"{prefix}{url}"
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
