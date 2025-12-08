import json
import logging
import os
from typing import TypedDict

import httpx
from langchain_core.tools import tool as make_tool, Tool
from langchain_core.runnables import RunnableConfig
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.graph_config import RetrievalType
import requests

# --- 该工具需要以下环境变量 ---
# RAGFLOW_API_BASE: RagFlow主API服务的访问方式，截止到版本且结尾不包含斜杠/


__all__ = ["KnowledgeTool"]
logger = logging.getLogger(__name__)


def _create_tool_description(f):
    tool = make_tool(f, parse_docstring=True)
    return dict(description=tool.description, args_schema=tool.args_schema)


class KnowledgeTool:
    """A langchain Knowledge tool to access knowledge base of RagFlow."""

    @staticmethod
    async def async_knowledge_retrieve(question: str, config: RunnableConfig):
        """Async version of `KnowledgeTool.sync_knowledge_retrieve`."""
        logger.info(f"开始执行知识检索流程，待检索的问题: {question}")
        api_base = _get_api_base(question)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(**_make_request_args(question, api_base, config))
            return _handle_response(response)
        except Exception as e:
            _log_exception(e, question)
            raise

    @staticmethod
    def sync_knowledge_retrieve(question: str, config: RunnableConfig):  # noqa: docstr use as tool schema
        """
        RAG流程核心检索工具：根据输入问题，从指定知识库中精准提取高相关性知识片段，
        为后续回答生成提供时效性、准确性、领域针对性的事实支撑，解决LLM知识过时、事实偏差问题。

        适用场景：
        - 领域专属问答（如法律条文查询、医疗指南解读、企业产品手册咨询）
        - 时效性问题检索（如最新行业数据、政策文件、赛事结果）
        - 长文档关键信息提取（如学术论文结论、白皮书核心观点）
        - 多轮对话上下文补充（关联历史提问的知识溯源与扩展）

        Args:
            question: str，必选参数
                - 功能：待检索知识的问题，支持单个问题检索
                - 约束：问题需为完整表意字符串，需包含关键实体（如"2024年 新能源汽车"）、
                  明确限定词（如"同比增长 监管政策"），避免模糊表述（如"这个怎么操作？"）
        """
        logger.info(f"开始执行知识检索流程，待检索的问题: {question}")
        api_base = _get_api_base(question)
        try:
            response = requests.post(**_make_request_args(question, api_base, config))
            return _handle_response(response)
        except Exception as e:
            _log_exception(e, question)
            raise

    knowledge_retrieve = Tool.from_function(func=sync_knowledge_retrieve, name="knowledge_retrieve",
                                            coroutine=async_knowledge_retrieve,
                                            **_create_tool_description(sync_knowledge_retrieve))


def _get_api_base(question: str) -> str:
    api_base = os.environ.get("RAGFLOW_API_BASE")
    if not api_base:
        logging.error(f"[EnvironError] 未配置RagFlow环境，检索终止 | Query: {question}")
        raise ValueError("RagFlow host information is not configured. Retrieval terminated.")
    return api_base


def _make_request_args(question: str, api_base: str, config: RunnableConfig) -> dict:
    rc = parse_research_config(config)
    retrieval_config = rc.retrieval_config
    if not retrieval_config or RetrievalType.RAGFLOW not in retrieval_config:
        raise ValueError("RagFlow retrieval config is not configured.")
    ragflow_retrieval_config = retrieval_config[RetrievalType.RAGFLOW]
    logger.info(f"对话ID: {ragflow_retrieval_config.args.dialog_id or '未知'}, 知识库IDs: {ragflow_retrieval_config.args.kb_ids}")

    kbs = ragflow_retrieval_config.args.kb_ids
    if not isinstance(kbs, list) and kbs:
        logger.error(f"未找到与对话 {ragflow_retrieval_config.args.dialog_id!r} 关联的知识库")
        raise RuntimeError(f"No knowledge bases found for dialog {ragflow_retrieval_config.args.dialog_id!r}")

    similarity_threshold = ragflow_retrieval_config.args.similarity_threshold
    rerank_enabled = bool(ragflow_retrieval_config.args.rerank_id)
    logger.info(f"调用检索器进行知识检索，{len(kbs)}个知识库，top_n={ragflow_retrieval_config.args.top_n}，"
                f"相似度阈值={similarity_threshold}，"
                f"{'' if rerank_enabled else '未'}启用重排。")

    # 调用检索接口
    headers = {"Authorization": f"Bearer {ragflow_retrieval_config.api_key}"}
    params = dict(
        question=question,
        dataset_ids=kbs,
        document_ids=[],
        page=1,
        page_size=20,
        similarity_threshold=similarity_threshold,
        vector_similarity_weight=ragflow_retrieval_config.args.vector_similarity_weight,
        top_k=ragflow_retrieval_config.args.top_k or 1024,
        rerank_id=ragflow_retrieval_config.args.rerank_id,
        keyword=False
    )
    return dict(url=f"{api_base}/retrieval", headers=headers, json=params,
                timeout=60 if rerank_enabled else 30)


def _handle_response(response: httpx.Response | requests.Response) -> str:
    response.raise_for_status()
    response_body: dict = response.json()
    if not ((response_body.get("code") == 0) and
            isinstance(response_body.get("data"), dict) and
            isinstance(response_body["data"].get("chunks"), list)):
        raise RuntimeError(response_body.get("message") or f"连接到知识库时出现未知问题。{response_body=}")
    raw_chunks: list[dict] = response_body["data"]["chunks"]
    if len(raw_chunks):
        returns = [
            {
                "title": each.get("content"),
                "url": each.get("document_id"),
                "chunk_id": each.get("id"),
                "content_with_weight": each.get("content"),
                "doc_id": each.get("document_id"),
                "docnm_kwd": each.get("document_keyword"),
                "kb_id": each.get("dataset_id"),
                "image_id": each.get("image_id"),
                "similarity": each.get("similarity"),
                "positions": each.get("positions"),
            }
            for each in raw_chunks
        ]
    else:
        logger.warning("未检索到任何知识片段")
        returns = []
    return json.dumps(returns, indent=4, ensure_ascii=False)


def _log_exception(e: Exception, question: str) -> None:
    if isinstance(e, (httpx.ConnectTimeout, requests.ConnectTimeout)):
        logging.error(f"[TimeoutError] 搜索请求超时: 连接或读取超时 - {e} | Query: {question}")
    elif isinstance(e, (httpx.ConnectError, requests.ConnectionError)):
        logging.error(f"[NetworkError] 网络连接失败: 无法连接到RagFlow服务 - {e} | Query: {question}")
    elif isinstance(e, (httpx.HTTPError, requests.HTTPError)):
        logging.error(f"[HTTPError] 未知HTTP错误: {e} | Query: {question}")
    logging.error(f"[UnknownError] 搜索处理过程中发生未知错误: {e} | Query: {question}")
