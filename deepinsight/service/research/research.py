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

from re import U
import uuid
import os
import io
import asyncio
from typing import AsyncGenerator, Any, Dict, List, Optional, Set, Tuple
import json
import base64
import logging
from datetime import datetime

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langfuse.langchain import CallbackHandler

from deepinsight.config.config import Config
from deepinsight.core.prompt.prompt_manager import PromptManager
from deepinsight.service.schemas.streaming import StreamEvent
from deepinsight.service.streaming.stream_adapter import StreamEventAdapter
from deepinsight.service.ppt.template_service import PPTTemplateService
from deepinsight.utils.file_storage.mem_fs import RootFileSystem
from deepinsight.utils.llm_utils import init_langchain_models_from_llm_config
from deepinsight.utils.common import safe_get
from deepinsight.core.agent.conf_chat.supervisor import graph as conference_qa_graph
from deepinsight.core.agent.conf_gen.supervisor import graph as conference_research_graph
from deepinsight.core.agent.conf_gen.cross_topic_supervisor import graph as cross_topic_graph
from deepinsight.core.agent.resch_gen.supervisor import graph as deep_research_graph
from deepinsight.core.agent.resch_gen.parallel_supervisor import graph as parallel_deep_research_graph
from deepinsight.core.agent.conf_gen.ppt_generate import graph as ppt_generate_graph
from deepinsight.core.types.graph_config import RetrievalConfig, RetrievalArgs, RetrievalType
from deepinsight.core.types.conference_constants import ConferenceFileNames, ConferenceFolderNames
from deepinsight.service.schemas.research import ResearchRequest, SceneType, PPTGenerateRequest, PdfGenerateRequest, ArgOptionsGeneric, LLMConfig
from deepinsight.utils.trans_md_to_pdf import save_markdown_as_pdf


class ResearchService:
    """
    Research graph service (streaming execution).

    - Decoupled from conference; usable for deep_research and others
    - Builds graph_config using request-first precedence, falling back to Config
    - Adapts graph streaming outputs to unified StreamEvent via StreamEventAdapter
    """

    def __init__(self, config: Config):
        self.config = config
        # Suppress specific tool names from streaming as thinking chunks
        self._blocked_tool_names = {
            "ClarifyWithUser",
            "ConductResearch",
            "ResearchComplete",
            "think_tool",
        }
        # Cached per-request filters (populated in _build_graph_config)
        self._text_block_nodes: Set[str] = set()
        self._tool_call_block_nodes: Set[str] = set()

    def _load_deep_research_options(self) -> Any:
        """Load typed deep_research configuration from scenarios config."""
        return safe_get(self.config, lambda c: c.scenarios.deep_research, None)

    def _build_graph_config(self, req: ResearchRequest, ragflow_authorization: Optional[str] = None,
                            *, file_system: RootFileSystem) -> dict:
        """Build a graph_config with request-first precedence, falling back to config.yaml."""
        # Prefer request-provided LLM configs, else use system defaults (wrapped)
        model_configs = req.args.llm_options if (
            getattr(req, "args", None) and getattr(req.args, "llm_options", None)
        ) else self.get_default_config()
        models, default_model = init_langchain_models_from_llm_config(model_configs)

        # Read scenario-specific flags and filters (typed access) with request override
        deep_cfg = self._load_deep_research_options()
        allow_user_clarification = req.allow_user_clarification if req.allow_user_clarification is not None else bool(safe_get(deep_cfg, lambda o: o.allow_user_clarification, False))
        allow_edit_research_brief = req.allow_edit_research_brief if req.allow_edit_research_brief is not None else bool(safe_get(deep_cfg, lambda o: o.allow_edit_research_brief, False))
        allow_edit_report_outline = req.allow_edit_report_outline if req.allow_edit_report_outline is not None else bool(safe_get(deep_cfg, lambda o: o.allow_edit_report_outline, False))
        final_report_model = req.final_report_model if getattr(req, "final_report_model", None) is not None else safe_get(deep_cfg, lambda o: o.final_report_model, None)

        if req.scene_type == SceneType.DEEP_RESEARCH:
            prompt_group = "resch_gen"
        elif req.scene_type == SceneType.CONFERENCE_RESEARCH:
            prompt_group = "conf_gen_supervisor"
        elif req.scene_type == SceneType.CONFERENCE_QA:
            prompt_group = "conf_chat"
        elif req.scene_type == SceneType.CROSS_TOPIC_RESEARCH:
            # 跨会议主题分析使用 conf_gen_cross_topic 提示词组
            prompt_group = "conf_gen_cross_topic"
        else:
            raise ValueError(f"Unknown scene type: {req.scene_type}")

        # Load base stream_blocklist from config (common configuration)
        stream_filter_text: Dict[str, bool] = safe_get(
            deep_cfg, lambda o: safe_get(o.stream_blocklist, lambda s: s.text, None), None
        ) or {}

        stream_filter_tool_call: Dict[str, bool] = safe_get(
            deep_cfg, lambda o: safe_get(o.stream_blocklist, lambda s: s.tool_call, None), None
        ) or {}

        # Extend blocklist for specific scene types (conference_qa / conference_research / cross_topic_research)
        if req.scene_type in [SceneType.CONFERENCE_QA, SceneType.CONFERENCE_RESEARCH, SceneType.CROSS_TOPIC_RESEARCH]:
            # Additional filters for conference scenes (only new ones not in config.yaml)
            conference_additional_filters = {
                "researcher_tools": True,
                "researcher": True,
                "publish_result": True,
                "generate_report": True,
                "generate_report_outline": True,
                "tools": True,
                "model": True,
                "agent": True,
            }
            # Merge additional filters with base configuration
            stream_filter_text.update(conference_additional_filters)
            stream_filter_tool_call.update(conference_additional_filters)

        # Build block lists from filter config (truthy values mean "block/suppress")
        self._text_block_nodes = {k for k, v in stream_filter_text.items() if v}
        self._tool_call_block_nodes = {k for k, v in stream_filter_tool_call.items() if v}

        run_id = str(uuid.uuid4())
        graph_config = {
            # Global run identifiers used by the streaming adapter
            "run_id": run_id,
            # LangGraph configurable section consumed by nodes/tools
            "configurable": {
                "thread_id": req.conversation_id,
                "run_id": run_id,
                "file_system": file_system,
                "models": models,
                "default_model": default_model,
                "llm_max_tokens": 8192,
                "max_structured_output_retries": 3,
                # Interactive interruptions per configuration
                "allow_user_clarification": allow_user_clarification,
                "allow_edit_research_brief": allow_edit_research_brief,
                "allow_edit_report_outline": allow_edit_report_outline,
                # Optional final report generation model hint
                "final_report_model": final_report_model,
                # Prompt group hint for graph implementation (generic)
                "prompt_group": prompt_group,
                # Provide PromptManager instance so graph nodes can fetch prompts
                "prompt_manager": PromptManager(self.config.prompt_management),
                "search_api": req.convert_search_type_to_search_api(),
                # Working path from global config (workspace.work_root), absolute for consistency
                "work_root": os.path.abspath(self.config.workspace.work_root) if getattr(self.config, "workspace", None) else None,
                # Relative image folder under work_root for chart outputs
                "chart_image_dir": getattr(self.config.workspace, "chart_image_dir", None),
                "enable_expert_review": req.expert_review_enable,
                "write_experts": req.write_experts,
            },
            # Keep recursion_limit aligned with typical graph defaults
            "recursion_limit": 1000,
            "callbacks": [CallbackHandler()],
        }

        # Process retrieval options for RAG if provided
        # Store in dict format keyed by retrieval type
        if "rag_retrieval" in req.search_type and req.args and req.args.retrieval_options:
            # Map retrieval_type string to RetrievalType enum
            type_mapping = {
                "ragflow": RetrievalType.RAGFLOW,
                "lightrag": RetrievalType.LIGHTRAG,
                "llamaindex": RetrievalType.LLAMAINDEX,
            }
            
            # Initialize retrieval_config dict if not exists
            if "retrieval_config" not in graph_config["configurable"]:
                graph_config["configurable"]["retrieval_config"] = {}
            
            # Process each retrieval option
            for retrieval_option in req.args.retrieval_options:
                retrieval_type_str = retrieval_option.type
                retrieval_params = retrieval_option.params
                
                retrieval_type_enum = type_mapping.get(retrieval_type_str.lower())
                if retrieval_type_enum:
                    # Create nested args structure
                    retrieval_args = RetrievalArgs(
                        dialog_id=retrieval_params.dialog_id,
                        kb_ids=retrieval_params.dataset_ids,
                        document_ids=retrieval_params.document_ids,
                        page=retrieval_params.page,
                        page_size=retrieval_params.page_size,
                        similarity_threshold=retrieval_params.similarity_threshold,
                        vector_similarity_weight=retrieval_params.vector_similarity_weight,
                        top_k=retrieval_params.top_k,
                        top_n=retrieval_params.top_n,
                        rerank_id=retrieval_params.rerank_id,
                        keyword=retrieval_params.keyword,
                        highlight=retrieval_params.highlight,
                    )
                    
                    # Create retrieval config with nested args
                    retrieval_config = RetrievalConfig(
                        type=retrieval_type_enum,
                        api_key=ragflow_authorization if retrieval_type_str == "ragflow" else None,
                        args=retrieval_args,
                    )
                    
                    # Store in dict keyed by retrieval type string
                    graph_config["configurable"]["retrieval_config"][retrieval_type_enum] = retrieval_config

        if (req.expert_review_enable or req.parallel_expert_review_enable) and req.review_experts:
            graph_config["configurable"]["expert_defs"] = [
                dict(
                    name=name,
                    prompt_key=name,
                ) for name in req.review_experts
            ]

        return graph_config

    def _select_scene_graph(self, request: ResearchRequest) -> CompiledStateGraph:
        """根据场景类型选择对应的 LangGraph。"""
        scene_type = request.scene_type or SceneType.DEEP_RESEARCH
        if scene_type == SceneType.CONFERENCE_QA:
            return conference_qa_graph
        elif scene_type == SceneType.CONFERENCE_RESEARCH:
            return conference_research_graph
        elif scene_type == SceneType.CROSS_TOPIC_RESEARCH:
            return cross_topic_graph
        elif scene_type == SceneType.DEEP_RESEARCH:
            if request.parallel_expert_review_enable and request.review_experts:
                return parallel_deep_research_graph
            return deep_research_graph
        raise ValueError(f"未知场景类型: {scene_type}")

    async def chat(
        self,
        *,
        request: ResearchRequest,
        ragflow_authorization: Optional[str] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Execute the research chat and yield StreamEvent.
    
        Parameters:
        - request: ResearchRequest with conversation_id, messages and optional args
        - ragflow_authorization: Optional authorization token for RAG services
        - scene_type: 从请求中读取，选择对应的 graph
        """
        disk_path = os.path.join(self.config.workspace.work_root, "conference_report_result", request.conversation_id)
        file_system = RootFileSystem.from_local_disk(disk_path)
        graph_config = self._build_graph_config(request, ragflow_authorization, file_system=file_system)
        adapter = StreamEventAdapter(
            text_stream_block_nodes=self._text_block_nodes or None,
            tool_call_stream_block_nodes=self._tool_call_block_nodes or None,
            blocked_tool_names=self._blocked_tool_names,
        )
        # 根据场景选择 graph
        scene_graph = self._select_scene_graph(request)
        async for event in adapter.run_graph(
            graph=scene_graph,
            messages=request.messages,
            graph_config=graph_config,
            conversation_id=request.conversation_id,
        ):
            yield event
        file_system.export_to_local_disk(disk_path)

    async def ppt_generate(
        self,
        *,
        request: PPTGenerateRequest,
    ) -> Tuple[io.BytesIO, str]:
        """
        Generate PPT based on the conversation history.
    
        Parameters:
        - request: PPTGenerateRequest with conversation_id and optional args
        """
        # 选择模型配置：优先使用请求参数中的 llm_options，其次使用全局配置
        model_configs = request.args.llm_options if (
            getattr(request, "args", None) and getattr(request.args, "llm_options", None)
        ) else self.get_default_config()
        if len(model_configs) == 0:
            raise ValueError(f"Provide at least one LLM configuration")
        models, default_model = init_langchain_models_from_llm_config(model_configs)
        run_id = str(uuid.uuid4())
        graph_result = await ppt_generate_graph.ainvoke(
            input={
            },
            config={
                # Global run identifiers used by the streaming adapter
                "run_id": run_id,
                "configurable": {
                    "thread_id": request.conversation_id,
                    "run_id": run_id,
                    "file_system": RootFileSystem.from_empty(),  # todo: implements later
                    "models": models,
                    "default_model": default_model,
                    "prompt_manager": PromptManager(self.config.prompt_management),
                    "prompt_group": "conf_gen_ppt_generate",
                    # Working path from global config (workspace.work_root), absolute for consistency
                    "work_root": os.path.abspath(self.config.workspace.work_root) if getattr(self.config, "workspace", None) else None,
                    # Relative image folder under work_root for chart outputs
                    "chart_image_dir": getattr(self.config.workspace, "chart_image_dir", None),
                },
                "callbacks": [CallbackHandler()],
            }
        )
        ppt_content_json_path = graph_result["ppt_json_file_path"]
        output_name = graph_result["ppt_generate_file_name"]

        # 从配置读取会议洞察报告 PPT 模板路径
        template_path = getattr(self.config.workspace, "conference_ppt_template_path", None)
        if not template_path:
            raise ValueError(
                "未配置 PPT 模板路径：请在 config.yaml 的 workspace.conference_ppt_template_path 指定模板文件"
            )

        ppt_template_service = PPTTemplateService()
        prs = ppt_template_service.fill_from_json_file(template_path, ppt_content_json_path)

        # 将pptx保存到BytesIO流中
        pptx_stream = io.BytesIO()
        prs.save(pptx_stream)
        pptx_stream.seek(0)
        return pptx_stream, output_name

    async def pdf_generate(self, request: PdfGenerateRequest):
        conversation_id = request.conversation_id
        model_configs = request.args.llm_options if (
                request.args and request.args.llm_options) else self.get_default_config()
        if len(model_configs) == 0:
            raise ValueError(f"Provide at least one LLM configuration")
        models, default_model = init_langchain_models_from_llm_config(llm_config=model_configs)
        work_root = os.path.abspath(self.config.workspace.work_root) if getattr(self.config, "workspace", None) else os.path.abspath("./data")
        base_dir = os.path.join(work_root, "conference_report_result", conversation_id)
        os.makedirs(base_dir, exist_ok=True)
        json_path = os.path.join(base_dir, "pdf_content.json")

        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                cached = json.loads(f.read())
            file_name = cached.get("file_name")
            pdf_bytes = base64.b64decode(cached.get("content", ""))
            buffer = io.BytesIO(pdf_bytes)
            buffer.seek(0)
            return buffer, file_name

        # 判断是否是跨会议主题分析
        cross_topic_statistics_path = os.path.join(base_dir, ConferenceFileNames.CROSS_TOPIC_STATISTICS_MD)
        cross_topic_summary_path = os.path.join(base_dir, ConferenceFileNames.CROSS_TOPIC_SUMMARY_MD)
        is_cross_topic = os.path.exists(cross_topic_statistics_path) or os.path.exists(cross_topic_summary_path)

        markdown_parts = []
        report_name = "未知报告"

        if is_cross_topic:
            # 跨会议主题分析的PDF生成逻辑
            # 1. 统计信息
            if os.path.exists(cross_topic_statistics_path):
                with open(cross_topic_statistics_path, "r", encoding="utf-8") as f:
                    markdown_parts.append(f.read())

            # 2. 多篇论文分析（按文件名排序）
            cross_topic_papers_dir = os.path.join(base_dir, ConferenceFolderNames.CROSS_TOPIC_PAPERS)
            if os.path.exists(cross_topic_papers_dir):
                paper_files = sorted([f for f in os.listdir(cross_topic_papers_dir) if f.endswith(".md")])
                for paper_file in paper_files:
                    paper_path = os.path.join(cross_topic_papers_dir, paper_file)
                    with open(paper_path, "r", encoding="utf-8") as f:
                        markdown_parts.append(f.read())

            # 3. 总结
            if os.path.exists(cross_topic_summary_path):
                with open(cross_topic_summary_path, "r", encoding="utf-8") as f:
                    markdown_parts.append(f.read())

            # 4. 论文列表
            papers_list_path = os.path.join(base_dir, "papers_list.md")
            if os.path.exists(papers_list_path):
                with open(papers_list_path, "r", encoding="utf-8") as f:
                    markdown_parts.append(f.read())

            # 提取报告名称（从统计信息或总结文件中提取主题和会议名称）
            extract_text = ""
            if os.path.exists(cross_topic_statistics_path):
                with open(cross_topic_statistics_path, "r", encoding="utf-8") as f:
                    extract_text = f.read()
            elif os.path.exists(cross_topic_summary_path):
                with open(cross_topic_summary_path, "r", encoding="utf-8") as f:
                    extract_text = f.read()

            if extract_text:
                prompt = (
                    "请从以下文本中提取研究主题和涉及的会议名称，格式为'主题_会议1_会议2'。"
                    "例如：如果主题是'分布式系统'，涉及'HOTOS 2025'和'OSDI 2025'，则返回'分布式系统_HOTOS-2025_OSDI-2025'。"
                    "仅返回提取的内容，不要包含其他文字。\n\n"
                    f"文本内容：\n{extract_text[:2000]}"  # 限制长度避免token过多
                )
                try:
                    response = await default_model.with_retry().ainvoke([HumanMessage(content=prompt)])
                    report_name = response.content.strip().replace("\n", "").replace("：", "_")
                except Exception as e:
                    logging.warning(f"LLM parse cross-topic report name error: {e}")
                    # 如果LLM解析失败，使用默认名称
                    report_name = "跨会议主题分析报告"

        else:
            # 原有的普通顶会分析逻辑
            ordered_files = [
                ConferenceFileNames.OVERVIEW_MD,
                ConferenceFileNames.KEYNOTES_MD,
                ConferenceFileNames.TOPIC_MD,
            ]
            value_mining_dir = os.path.join(base_dir, ConferenceFolderNames.VALUE_MINING)
            value_mining_files = [
                "tech_topics.md",
                "national_tech_profile.md",
                "institution_overview.md",
                "inter_institution_collab.md",
                "research_hotspots.md",
                "high_potential_tech_transfer.md",
            ]
            summary_file = ConferenceFileNames.SUMMARY_MD
            best_papers_dir = os.path.join(base_dir, ConferenceFolderNames.BEST_PAPERS)

            for file_name in ordered_files:
                file_path = os.path.join(base_dir, file_name)
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        markdown_parts.append(f.read())

            if os.path.exists(value_mining_dir):
                for vm_file in value_mining_files:
                    vm_path = os.path.join(value_mining_dir, vm_file)
                    if os.path.exists(vm_path):
                        with open(vm_path, "r", encoding="utf-8") as f:
                            markdown_parts.append(f.read())
                        
            if os.path.exists(best_papers_dir):
                best_papers = sorted(
                    [f for f in os.listdir(best_papers_dir) if f.endswith(".md")]
                )
                for bp in best_papers:
                    bp_path = os.path.join(best_papers_dir, bp)
                    with open(bp_path, "r", encoding="utf-8") as f:
                        markdown_parts.append(f.read())

            summary_path = os.path.join(base_dir, summary_file)
            if os.path.exists(summary_path):
                with open(summary_path, "r", encoding="utf-8") as f:
                    markdown_parts.append(f.read())

            # 提取会议名称
            overview_path = os.path.join(base_dir, ConferenceFileNames.OVERVIEW_MD)
            if os.path.exists(overview_path):
                with open(overview_path, "r", encoding="utf-8") as f:
                    overview_text = f.read()

                prompt = (
                    "请从以下文本中提取会议名称和年份，例如'SOSP 2025'或'NeurIPS 2024'。"
                    "仅返回会议名与年份，不要包含其他文字。\n\n"
                    f"文本内容：\n{overview_text}"
                )
                try:
                    response = await default_model.with_retry().ainvoke([HumanMessage(content=prompt)])
                    report_name = response.content
                    report_name = report_name.strip().replace("\n", "").replace("：", ":")
                except Exception as e:
                    logging.warning(f"LLM parse conference name error: {e}")

        if not markdown_parts:
            raise FileNotFoundError(f"No markdown files found for conversation_id={conversation_id}")

        merged_markdown = "\n\n---\n\n".join(markdown_parts)

        now = datetime.now()
        time_str = now.strftime("%Y年%m月%d日 %H时%M分%S秒")
        time_for_filename = now.strftime("%Y%m%d_%H%M%S")

        header_info = (
            f"作者：DeepInsight顶会助手v0.1.0  \n"
            f"部门：中软架构与设计管理部  \n"
            f"时间：{time_str}  \n\n---\n\n"
        )

        final_markdown = header_info + merged_markdown

        # 根据报告类型生成不同的文件名
        if is_cross_topic:
            file_name = f"{report_name}_跨会议主题分析报告-{time_for_filename}.pdf"
        else:
            file_name = f"{report_name} 洞察报告-{time_for_filename}.pdf"
        buffer = io.BytesIO()
        output_pdf_path = os.path.join(base_dir, file_name)
        await asyncio.to_thread(
            save_markdown_as_pdf,
            markdown_content=final_markdown,
            output_filename=output_pdf_path,
            base_url=base_dir,
        )
        pdf_bytes = await asyncio.to_thread(lambda p=output_pdf_path: open(p, "rb").read())
        buffer.write(pdf_bytes)
        buffer.seek(0)

        cache_data = {"file_name": file_name, "content": base64.b64encode(buffer.getvalue()).decode("utf-8")}
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(cache_data, ensure_ascii=False, indent=2))

        return buffer, file_name

    def get_default_config(self) -> List[ArgOptionsGeneric[LLMConfig]]:
        return [
            ArgOptionsGeneric(
                type=each.type,
                params=LLMConfig(
                    type=each.type,
                    model=each.model,
                    base_url=each.base_url,
                    api_key=each.api_key,
                    setting=each.setting,
                ),
            )
            for each in self.config.llms
        ]
