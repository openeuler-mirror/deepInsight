from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from typing import Dict, List, Optional, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc
from lightrag.llm.hf import hf_embed
from transformers import AutoModel, AutoTokenizer
from lightrag.kg.shared_storage import initialize_pipeline_status

from deepinsight.config.config import CONFIG, Config
from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.utils.llm_utils import init_lightrag_llm_model_func

from ..schemas.rag import DocumentPayload, IndexResult, Passage, DocProcessStatus


class RAGEngine:
    """Async local engine wrapper around LightRAG.

    Key points:
    - Cache LightRAG instances by `working_dir` to avoid re-initialization;
    - Use `ainsert` for async document parsing and ingestion (await until done);
    - Provide unified async semantic search returning standardized `Passage` list;
    - Align initialization and embedding behavior with KnowledgeService.
    """

    def __init__(self, config: Optional[Config] = None):
        self._rag_cache: Dict[str, LightRAG] = {}
        self._rag_initialized: set[str] = set()
        self._llm_func = None
        self._config = config or CONFIG

    async def _get_or_create_rag(self, working_dir: str) -> LightRAG:
        if not working_dir:
            raise ValueError("working_dir must not be empty")
        os.makedirs(working_dir, exist_ok=True)

        rag = self._rag_cache.get(working_dir)
        if rag is None:
            if self._llm_func is None:
                if self._config is None:
                    raise RuntimeError("Config not initialized; load config before using RAG engine")
                self._llm_func = init_lightrag_llm_model_func(self._config)

            rag = LightRAG(
                working_dir=working_dir,
                llm_model_func=self._llm_func,
                embedding_func=EmbeddingFunc(
                    embedding_dim=384,
                    func=lambda texts: hf_embed(
                        texts,
                        tokenizer=AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2"),
                        embed_model=AutoModel.from_pretrained("sentence-transformers/all-MiniLM-L6-v2"),
                    ),
                ),
            )
            self._rag_cache[working_dir] = rag

        if working_dir not in self._rag_initialized:
            await rag.initialize_storages()
            await initialize_pipeline_status()
            self._rag_initialized.add(working_dir)
        return rag

    async def ingest_document(self, doc: DocumentPayload, working_dir: str,
                              make_knowledge_graph=False) -> IndexResult:
        """Parse and index a document (fully async).

        Behavior:
        - If `raw_text` is provided, ingest directly;
        - Else read from `source_path` using LangChain document loaders;
        - Return standardized `IndexResult` including parsed documents list.
        """
        rag = await self._get_or_create_rag(working_dir)

        logging.debug("Begin parse document (id=%r) at %r.", doc.doc_id, doc.source_path)
        if not make_knowledge_graph:
            async def no_extract(*_args, **_kwargs):
                return "<|COMPLETE|>"  # Only for LightRAG. Update it when LightRAG changes its implementation
            rag.llm_model_func = no_extract

        documents_data: List[dict] = []
        if doc.raw_text and doc.raw_text.strip():
            text = doc.raw_text
            file_paths = [doc.source_path] if doc.source_path else None
            # Build a single LangChain-like document for downstream usage
            try:
                from langchain_core.documents import Document as LCDocument
                lc_doc = LCDocument(page_content=text, metadata={"source": doc.source_path or "inline"})
                documents_data = [{"page_content": lc_doc.page_content, "metadata": lc_doc.metadata}]
            except Exception:
                documents_data = [{"page_content": text, "metadata": {"source": doc.source_path or "inline"}}]
            # Pass relational DB document id into LightRAG for linkage
            await rag.ainsert([text], ids=[doc.doc_id], file_paths=file_paths)
            chunks_count = _estimate_chunks(text)
            process_status = await self._fetch_doc_status(rag, str(doc.doc_id))
        else:
            if not doc.source_path or not os.path.isfile(doc.source_path):
                raise ValueError("raw_text not provided and source_path missing or unreadable")
            # Use LangChain document loaders to parse various file types
            docs_with_resource = await _load_langchain_documents(doc.source_path)
            if docs_with_resource.images:
                await _store_images(working_dir, doc.doc_id, doc.source_path, docs_with_resource.images)
                await _replace_image_link(docs_with_resource, replace_alt_text=True,
                                          prefix=os.path.join("..", "..", working_dir, doc.doc_id))
            docs = docs_with_resource.text
            documents_data = [{"page_content": d.page_content, "metadata": getattr(d, "metadata", {})} for d in docs]
            text = "\n\n".join(d["page_content"] for d in documents_data if d.get("page_content"))
            # Pass relational DB document id into LightRAG for linkage
            await rag.ainsert([text], ids=[doc.doc_id], file_paths=[doc.source_path])
            chunks_count = _estimate_chunks(text)
            process_status = await self._fetch_doc_status(rag, str(doc.doc_id))

        logging.debug("Parse document (id=%r) at %r done. Got %s chunks.",
                      doc.doc_id, doc.source_path, chunks_count)
        return IndexResult(
            doc_id=doc.doc_id,
            indexed=True,
            chunks_count=chunks_count,
            extracted_text=text,
            documents=documents_data,
            process_status=process_status,
        )

    async def semantic_search(self, working_dir: str, query: str, top_k: int = 8) -> List[Passage]:
        """Unified async semantic search.

        To be compatible with different LightRAG versions, we adapt method names:
        - Prefer `asearch`, fallback to `aquery`, finally try sync `search` in a thread.
        Results are best-effort mapped to `Passage`.
        """
        rag = await self._get_or_create_rag(working_dir)

        # Call appropriate search method based on availability
        result: Any
        if hasattr(rag, "asearch"):
            result = await rag.asearch(query, top_k=top_k)
        elif hasattr(rag, "aquery"):
            result = await rag.aquery(query, top_k=top_k)
        elif hasattr(rag, "search"):
            import asyncio
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: rag.search(query, top_k=top_k))
        else:
            result = []

        passages: List[Passage] = []
        # Normalize result forms: list[str] / list[dict] / custom objects
        base_doc_id = f"kb:{os.path.basename(working_dir) or 'default'}"
        if isinstance(result, list):
            for i, item in enumerate(result):
                if isinstance(item, str):
                    passages.append(Passage(doc_id=base_doc_id, chunk_id=f"res:{i}", text=item, score=0.0, meta={}))
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or str(item)
                    score = float(item.get("score", 0.0))
                    meta = {k: v for k, v in item.items() if k not in {"text", "content", "score"}}
                    passages.append(Passage(doc_id=base_doc_id, chunk_id=f"res:{i}", text=text, score=score, meta=meta))
                else:
                    txt = getattr(item, "text", None) or getattr(item, "content", None) or str(item)
                    scor = float(getattr(item, "score", 0.0))
                    passages.append(Passage(doc_id=base_doc_id, chunk_id=f"res:{i}", text=txt, score=scor, meta={}))
        return passages[:top_k]

    async def _fetch_doc_status(self, rag: LightRAG, doc_id: str) -> Optional[DocProcessStatus]:
        try:
            res = await rag.aget_docs_by_ids([doc_id])

            if isinstance(res, dict):
                item = res.get(doc_id) or next(iter(res.values()), None)
            elif isinstance(res, list):
                item = res[0] if res else None
            else:
                item = res

            if item is None:
                return DocProcessStatus.failed

            status = item["status"]
            if not status:
                return DocProcessStatus.failed

            s = None
            if isinstance(status, str):
                s = status.lower()
            else:
                s = getattr(status, "value", None)
                if isinstance(s, str):
                    s = s.lower()
                else:
                    # Fallback to string repr
                    s = str(status).lower()

            if s in {"pending", "processing", "preprocessed"}:
                return DocProcessStatus.processing
            if s in {"processed"}:
                return DocProcessStatus.parsed
            if s in {"failed"}:
                return DocProcessStatus.failed
            return DocProcessStatus.failed
        except Exception:
            return DocProcessStatus.failed


# --------- Robust text IO helpers (aligned with KnowledgeService behavior) ---------

async def _load_langchain_documents(file_path: str) -> ParseResult:
    """Load documents via LangChain loaders for various file types.
    Returns a list of LangChain `Document` objects; falls back to a single Document with extracted text.
    """
    ext = os.path.splitext(file_path.lower())[1]
    doc_with_resource: ParseResult | None = None
    docs: List[Any] = []
    try:
        if ext in {".pdf", ".docx", ".doc", ".pptx", ".ppt"}:
            try:
                from deepinsight.service.rag.loaders.mineru_online import MinerUOnlineClient
                with open(file_path, mode="rb") as f:
                    doc_with_resource = await MinerUOnlineClient().process(os.path.basename(file_path), f.read())
            except Exception as e:
                logging.error(f"Failed to parse {file_path!r} using MinerU with {type(e).__name__}: {e}")
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
            # Default: try text loader
            try:
                from langchain_community.document_loaders import TextLoader
                loader = TextLoader(file_path, encoding="utf-8")
                docs = loader.load()
            except Exception:
                docs = []
    except Exception:
        docs = []

    if not (docs or doc_with_resource):
        # Fallback to legacy text extraction and wrap into a single Document
        logging.warning(f"Extraction on file {file_path} failed, try regard it as a file with plain text.")
        text = _extract_text(file_path)
        from langchain_core.documents import Document as LCDocument
        return ParseResult(text=[LCDocument(page_content=text, metadata={"source": file_path})])

    return doc_with_resource or ParseResult(text=docs)


def _extract_text(file_path: str) -> str:
    _, ext = os.path.splitext(file_path.lower())
    text_based_exts = {".txt", ".md", ".markdown"}
    try:
        if ext in text_based_exts:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        else:
            try:
                import textract  # lazy import to avoid hard dependency
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
        raise RuntimeError(f"Text extraction failed for {file_path}: {e}")


def _estimate_chunks(text: str) -> int:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return max(1, len(paragraphs))


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
            existed = "an unknown document"  # for conflict warning, it is an unknown document
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
                logging.warning(f"Image {file_path} already exists for {existed}, overwrite.")
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

        old_match = match.group(0)
        old_prefix = old_match[:match.start(1) - match.start()]
        old_middle = old_match[match.end(1) - match.start() : match.start(2) - match.start()]
        old_suffix = old_match[match.end(2) - match.start():]

        return old_prefix + new_alt + old_middle + new_path + old_suffix

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
- 对于内容为表格的图片，需转换为标准Markdown格式（例如：| 姓名 | 年龄 |\n|------|------|\n| 张三 | 25   |）
- 图片描述需要简洁客观，避免主观判断
- 不要解释或添加额外说明，直接返回JSON字符串
"""


async def _create_image_description_batch(images: dict[str, bytes], vl: BaseChatModel = None) -> dict[str, str | None]:
    if vl is None:
        logging.info("Create image description without a given VL model. Using default model.")
        try:
            vl = _create_image_desc_default_model()
        except Exception as e:
            logging.warning(f"Try create image description but {type(e).__name__} raised when create VL models."
                            f"Skip: {e}")
            return {}
    results = await asyncio.gather(*[
        _create_image_description(*i, vl=vl) for i in images.items()
    ])
    return dict(results)


async def _create_image_description(filename: str, image: bytes, vl: BaseChatModel) -> tuple[str, str | None]:
    file_format = filename.rsplit(".")[-1]
    url = dict(
        url=f"data:image/{file_format};base64," + base64.b64encode(image).decode("utf8"),
        detail = "high"
    )
    inputs = [
        HumanMessage(content=[
            dict(type="image_url", image_url=url),
            dict(type="text", text=_CREATE_IMAGE_DESC_PROMPT),
        ])
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
        logging.error(f"Create Image description failed with {type(e).__name__}: {e}", exc_info=True)
        return filename, None

def _create_image_desc_default_model() -> BaseChatModel:
    url = os.environ.get("RAG_VL_URL")
    key = os.environ.get("RAG_VL_API_KEY")
    model = os.environ.get("RAG_VL_MODEL")

    from langchain_openai import ChatOpenAI
    return ChatOpenAI(openai_api_base=url, openai_api_key=key, model=model)  # type: ignore
