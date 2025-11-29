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
from typing import AsyncGenerator, Any, Dict, Set

from langgraph.graph.state import CompiledStateGraph
from langfuse.langchain import CallbackHandler

from deepinsight.config.config import Config
from deepinsight.core.prompt.prompt_manager import PromptManager
from deepinsight.service.schemas.streaming import StreamEvent
from deepinsight.service.streaming.stream_adapter import StreamEventAdapter
from deepinsight.service.ppt.template_service import PPTTemplateService
from deepinsight.utils.llm_utils import init_langchain_models_from_llm_config
from deepinsight.utils.common import safe_get
from deepinsight.core.agent.conference_research.supervisor import graph as conference_graph
from deepinsight.core.agent.conference_research.ppt_generate import graph as ppt_generate_graph
from deepinsight.service.schemas.research import ResearchRequest, SceneType, PPTGenerateRequest


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

    def _build_graph_config(self, req: ResearchRequest) -> dict:
        """Build a graph_config with request-first precedence, falling back to config.yaml."""
        # Prefer request-provided LLM configs, else use system defaults
        model_configs = req.args.llm_options if (getattr(req, "args", None) and getattr(req.args, "llm_options", None)) else self.config.llms
        models, default_model = init_langchain_models_from_llm_config(model_configs)

        # Read scenario-specific flags and filters (typed access) with request override
        deep_cfg = self._load_deep_research_options()
        allow_user_clarification = req.allow_user_clarification if req.allow_user_clarification is not None else bool(safe_get(deep_cfg, lambda o: o.allow_user_clarification, False))
        allow_edit_research_brief = req.allow_edit_research_brief if req.allow_edit_research_brief is not None else bool(safe_get(deep_cfg, lambda o: o.allow_edit_research_brief, False))
        allow_edit_report_outline = req.allow_edit_report_outline if req.allow_edit_report_outline is not None else bool(safe_get(deep_cfg, lambda o: o.allow_edit_report_outline, False))
        final_report_model = req.final_report_model if getattr(req, "final_report_model", None) is not None else safe_get(deep_cfg, lambda o: o.final_report_model, None)
 
        # Determine prompt group for this scene
        # Conference graph expects prompts under the 'conference_supervisor' group module
        if (req.scene_type or SceneType.DEEP_RESEARCH) == SceneType.CONFERENCE:
            prompt_group = "conference_supervisor"
        else:
            # Fallback group name; supervisor graph is only used for conference
            prompt_group = "deepresearch"

        stream_filter_text: Dict[str, bool] = safe_get(
            deep_cfg, lambda o: safe_get(o.stream_blocklist, lambda s: s.text, None), None
        ) or {}

        stream_filter_tool_call: Dict[str, bool] = safe_get(
            deep_cfg, lambda o: safe_get(o.stream_blocklist, lambda s: s.tool_call, None), None
        ) or {}

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
                "search_api": req.search_api or [],
                # Working path from global config (workspace.work_root), absolute for consistency
                "work_root": os.path.abspath(self.config.workspace.work_root) if getattr(self.config, "workspace", None) else None,
                # Relative image folder under work_root for chart outputs
                "chart_image_dir": getattr(self.config.workspace, "chart_image_dir", None),
            },
            # Keep recursion_limit aligned with typical graph defaults
            "recursion_limit": 1000,
            "callbacks": [CallbackHandler()],
        }
        return graph_config

    def _select_scene_graph(self, scene_type: SceneType | str) -> CompiledStateGraph:
        """根据场景类型选择对应的 LangGraph。"""
        if scene_type == SceneType.CONFERENCE:
            return conference_graph
        elif scene_type == SceneType.DEEP_RESEARCH:
            # 目前暂无通用 research 图实现
            raise NotImplementedError("暂未支持 research 场景的 LangGraph；请使用 scene_type=conference")
        raise ValueError(f"未知场景类型: {scene_type}")

    async def chat(
        self,
        *,
        request: ResearchRequest,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Execute the research chat and yield StreamEvent.
    
        Parameters:
        - request: ResearchRequest with conversation_id, query and optional args
        - scene_type: 从请求中读取，选择对应的 graph
        """
        graph_config = self._build_graph_config(request)
        adapter = StreamEventAdapter(
            text_stream_block_nodes=self._text_block_nodes or None,
            tool_call_stream_block_nodes=self._tool_call_block_nodes or None,
            blocked_tool_names=self._blocked_tool_names,
        )
        # 根据场景选择 graph
        scene = request.scene_type or SceneType.DEEP_RESEARCH
        scene_graph = self._select_scene_graph(scene)
        async for event in adapter.run_graph(
            graph=scene_graph,
            query=request.query,
            graph_config=graph_config,
            conversation_id=request.conversation_id,
        ):
            yield event

    async def ppt_generate(
        self,
        *,
        request: PPTGenerateRequest,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Generate PPT based on the conversation history.
    
        Parameters:
        - request: PPTGenerateRequest with conversation_id and optional args
        """
        # 选择模型配置：优先使用请求参数中的 llm_options，其次使用全局配置
        model_configs = request.args.llm_options if (
            getattr(request, "args", None) and getattr(request.args, "llm_options", None)
        ) else self.config.llms
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
                    "models": models,
                    "default_model": default_model,
                    "prompt_manager": PromptManager(self.config.prompt_management),
                    "prompt_group": "conference_ppt_generate",
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