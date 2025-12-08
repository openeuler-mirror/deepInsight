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

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RAGEngineType(str, Enum):
    lightrag = "lightrag"
    llamaindex = "llamaindex"


class RAGParserType(str, Enum):
    mineru_vl = "mineru_vl"
    llamaindex = "llamaindex"


class LightRAGEngineConfig(BaseModel):
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="HuggingFace embedding model name used by LightRAG.",
    )
    embedding_dim: int = Field(
        default=384,
        description="Embedding dimension for the selected model.",
    )
    enable_graph_extraction: bool = Field(
        default=False,
        description="Whether to allow LightRAG to construct knowledge graphs during ingestion.",
    )


class LlamaIndexEngineConfig(BaseModel):
    embed_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Embedding model name for LlamaIndex vectorization.",
    )
    embed_device: str = Field(
        default="cpu",
        description="Device placement for embeddings, e.g. cpu / cuda:0.",
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="LLM name used by LlamaIndex query engine; falls back to config.llms if unset.",
    )
    llm_api_key: Optional[str] = Field(
        default=None,
        description="Override API key for the configured LlamaIndex LLM. Uses env/config defaults when empty.",
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        description="Override base url for the configured LlamaIndex LLM. Uses env/config defaults when empty.",
    )


class RAGEngineConfig(BaseModel):
    type: RAGEngineType = Field(
        default=RAGEngineType.lightrag,
        description="Backend implementation used for retrieval augmented generation.",
    )
    lightrag: LightRAGEngineConfig = Field(
        default_factory=LightRAGEngineConfig,
        description="LightRAG specific configuration.",
    )
    llamaindex: LlamaIndexEngineConfig = Field(
        default_factory=LlamaIndexEngineConfig,
        description="LlamaIndex specific configuration.",
    )


class MineruParserConfig(BaseModel):
    enable_vl: bool = Field(
        default=True,
        description="Enable image caption generation via vision-language model.",
    )


class LlamaIndexParserConfig(BaseModel):
    use_llama_parse: bool = Field(
        default=False,
        description="Enable LlamaParse for supported file types when parsing via LlamaIndex.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Optional override for LlamaParse API key. Defaults to LLAMA_PARSE_API_KEY env.",
    )
    premium: bool = Field(
        default=False,
        description="Whether to use LlamaParse premium mode.",
    )


class RAGParserConfig(BaseModel):
    type: RAGParserType = Field(
        default=RAGParserType.mineru_vl,
        description="Document parsing pipeline used before indexing.",
    )
    mineru_vl: MineruParserConfig = Field(
        default_factory=MineruParserConfig,
        description="MinerU + VL parsing configuration.",
    )
    llamaindex: LlamaIndexParserConfig = Field(
        default_factory=LlamaIndexParserConfig,
        description="LlamaIndex parsing configuration.",
    )


class RAGConfig(BaseModel):
    """RAG 相关配置，包含工作目录、解析与索引后端"""

    work_root: str = Field(
        default=".",
        description="Base working path prefix for RAG data and storage",
    )
    engine: RAGEngineConfig = Field(
        default_factory=RAGEngineConfig,
        description="RAG backend engine configuration.",
    )
    parser: RAGParserConfig = Field(
        default_factory=RAGParserConfig,
        description="Document parsing pipeline configuration.",
    )