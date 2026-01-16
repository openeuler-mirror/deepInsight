"""
Conference Management Command

This module provides CLI for managing conference information: list, remove, generate KB.
"""

import argparse
import sys
import os
from typing import Optional
import logging
import asyncio
import re
from rich import get_console
from rich.live import Live


from deepinsight.utils.progress import RichProgressReporter

from deepinsight.service.conference import ConferenceService
from deepinsight.config.config import load_config, Config
from deepinsight.service.schemas.conference import (
    ConferenceListRequest,
    ConferenceDeleteRequest,
    ConferenceListResponse,
    ConferenceParseDocsRequest,
)

logger = logging.getLogger(__name__)


class ConferenceCommand:
    """Conference management command handler"""

    # Initialize service instance lazily
    def __init__(self):
        self._service: Optional[ConferenceService] = None
        # Cache config to avoid duplicate loads across methods
        self._config = None

    def execute(self, args: argparse.Namespace) -> int:
        # Parse conference-specific arguments
        parser = self._create_parser()
        conf_args = parser.parse_args(sys.argv[2:])  # Skip 'deepinsight conference'

        if conf_args.subcommand == 'list':
            return self._handle_list(conf_args)
        elif conf_args.subcommand == 'remove':
            return self._handle_remove(conf_args)
        elif conf_args.subcommand == 'gen':
            return self._handle_generate(conf_args)
        elif conf_args.subcommand == 'chat':
            return self._handle_qa(conf_args)
        elif conf_args.subcommand == 'topic':
            return self._handle_topic(conf_args)
        else:
            parser.print_help()
            return 1

    def _create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog='di conf',
            description='Conference information management (list/remove/gen/chat)',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='''\
Examples:
  di conf list
  di conf gen --name "ICLR 2025" --files-src ./docs
  di conf chat --name "ICLR 2025" --files-src ./docs --question "今年最佳论文有哪些创新点？"
  di conf chat --name "HOTOS 2025, OSDI 2025" --files-src "./path1,./path2" --question "分布式系统一致性协议的研究进展"
  di conf topic --question "分布式系统一致性" --name "HOTOS 2025, OSDI 2024" --file-src "./path1,./path2"
  di conf remove --id 12
            '''
        )
        subparsers = parser.add_subparsers(dest='subcommand', help='Operations')

        # list
        list_parser = subparsers.add_parser('list', help='List conference records')
        # Short aliases: -s for --short-name, -y for --year, -L for --location, -n for --limit, -o for --offset
        list_parser.add_argument('--short-name', '-s', required=False, help='Filter by short name')
        list_parser.add_argument('--year', '-y', type=int, required=False, help='Filter by year')
        list_parser.add_argument('--location', '-L', required=False, help='Filter by location')
        list_parser.add_argument('--limit', '-n', type=int, default=100, help='Limit returned items (default: 100)')
        list_parser.add_argument('--offset', '-o', type=int, default=0, help='Offset (default: 0)')

        # remove
        remove_parser = subparsers.add_parser('remove', help='Remove a conference')
        # Support both positional ID and optional alias -i/--id for convenience
        remove_parser.add_argument('conference_id', nargs='?', type=int, help='Conference ID')
        remove_parser.add_argument('--id', '-i', type=int, help='Conference ID (optional flag)')

        # gen (generate)
        gen_parser = subparsers.add_parser('gen', help='Generate conference knowledge base (auto rollback on failure)')
        gen_parser.add_argument('--name', '-n', required=True, help='Conference name including year, e.g., "ICLR 2025"')
        gen_parser.add_argument('--files-src', '-f', required=True, help='User-provided source directory of files to ingest')
        
        # chat (qa)
        chat_parser = subparsers.add_parser('chat', help='Conference QA based on ingested documents')
        chat_parser.add_argument('--name', '-n', required=True, help='Conference name(s) including year, comma-separated for multiple conferences, e.g., "ICLR 2025" or "HOTOS 2025, OSDI 2025"')
        chat_parser.add_argument('--files-src', '-f', required=True, help='Source directory(ies) of files to ingest, comma-separated for multiple conferences, e.g., "./docs" or "./path1,./path2"')
        chat_parser.add_argument('--question', '-q', required=True, help='User question to answer against the conference knowledge base')
        
        # topic (cross-conference topic analysis)
        topic_parser = subparsers.add_parser('topic', help='Cross-conference topic analysis')
        topic_parser.add_argument('--question', '-q', required=True, help='Research question/topic to analyze across conferences')
        topic_parser.add_argument('--name', '-n', required=True, help='Comma-separated conference names with years, e.g., "HOTOS 2025, OSDI 2024"')
        topic_parser.add_argument('--file-src', '-f', required=True, help='Comma-separated paths to conference paper directories, e.g., "./path1,./path2"')
        
        return parser

    def _get_config(self):
        if self._config is None:
            cfg_path = os.getenv('DEEPINSIGHT_CONFIG', os.path.join(os.getcwd(), 'config.yaml'))
            self._config = load_config(cfg_path)
        return self._config

    def _get_service(self) -> ConferenceService:
        if self._service is None:
            config = self._get_config()
            from deepinsight.utils.file_storage import get_storage_impl
            get_storage_impl(config)
            self._service = ConferenceService(config)
        return self._service

    def _handle_list(self, args: argparse.Namespace) -> int:
        try:
            service = self._get_service()
            import asyncio
            listed: ConferenceListResponse = asyncio.run(service.list_conferences(ConferenceListRequest(
                short_name=args.short_name,
                year=args.year,
                location=args.location,
                limit=args.limit,
                offset=args.offset,
            )))
            for item in listed.items:
                print(f"{item.conference_id}\t{item.short_name or item.full_name}\t{item.year}")
            print(f"Count: {listed.count}")
            return 0
        except Exception as e:
            print(f"✗ 查询失败：{e}")
            return 1

    def _handle_remove(self, args: argparse.Namespace) -> int:
        try:
            service = self._get_service()
            # Prefer optional flag --id/-i; fallback to positional conference_id
            cid = args.id if getattr(args, 'id', None) is not None else args.conference_id
            if cid is None:
                raise ValueError('Conference ID is required (use positional or --id/-i)')
            import asyncio
            result = asyncio.run(service.delete_conference(ConferenceDeleteRequest(conference_id=cid)))
            print("Deleted" if result.ok else "Not found")
            return 0
        except Exception as e:
            print(f"✗ 删除失败：{e}")
            return 1

    def _handle_generate(self, args: argparse.Namespace) -> int:
        try:
            service = self._get_service()
            # Parse year from name (supports "ICLR 2025", "International Conference on ML (2025)", "ICML2025", etc.)
            name = args.name.strip()
            m = re.search(r'(19|20)\d{2}', name)
            if not m:
                raise ValueError('No year detected in name, please include a four-digit year like 2025')
            year = int(m.group(0))
            # Keep full name with year as provided by user
            full_name = name
            # Derive short_name from name without year wrappers
            base_name_no_year = re.sub(r'[\s\(\[\{,]*' + m.group(0) + r'[\s\)\]\},]*', ' ', name).strip()
            if not base_name_no_year:
                base_name_no_year = name.replace(m.group(0), '').strip()
            short_name = None
            compact = re.sub(r'\s+', '', base_name_no_year)
            if compact.isupper() and 2 <= len(compact) <= 12:
                short_name = compact
            req = ConferenceParseDocsRequest(
                short_name=short_name,
                full_name=full_name,
                year=year,
                docs_src_dir=args.files_src,
            )
            # Use global rich console to avoid interleaving logs and progress output
            reporter = RichProgressReporter(console=get_console())
            # English notice for potentially long parsing time
            reporter.info("Parsing documents. This may take a while...")
            conf_id, kb_id = asyncio.run(service.ensure_conference_and_ingest_docs(req, reporter=reporter))
            print("✓ 生成成功：会议文档已入库（创建或增量）")

            # --- Integrate research streaming to auto-generate a summary report ---
            from deepinsight.service.research.research import ResearchService
            from deepinsight.service.schemas.research import ResearchRequest, SceneType, PPTGenerateRequest, ResearchArgs, RetrievalArgs, ArgOptionsGeneric
            from deepinsight.service.schemas.streaming import Message, MessageContent, MessageContentType
            from deepinsight.cli.commands.stream import run_research_and_save_report_sync
            
            research_service = ResearchService(self._get_config())
            # Build a deterministic conversation id for this conference
            base = (short_name or full_name).strip()
            slug = re.sub(r"\s+", "-", base)
            conv_id = f"conf-{slug}-{year}"
            # Default query to summarize ingested conference documents
            query = f"请对{full_name}（{year}）的会议文档进行研究，总结主题、趋势与亮点，并生成综合报告。"
            # File stem for output (markdown/pdf)
            result_stem = f"conference_{(short_name or base_name_no_year).strip()}_{year}"
            # Execute streaming research and save report
            run_research_and_save_report_sync(
                service=research_service,
                request=ResearchRequest(
                    conversation_id=conv_id,
                    messages=[
                        Message(
                            content=MessageContent(text=query),
                            content_type=MessageContentType.plain_text,
                        )
                    ],
                    scene_type=SceneType.CONFERENCE_RESEARCH,
                    allow_user_clarification=False,
                    allow_edit_research_brief=False,
                    allow_edit_report_outline=False,
                    search_type=["rag_retrieval", "web_search"],  # Default: RAG + web search
                    args=ResearchArgs(
                        retrieval_options=self._create_retrieval_options(self._get_config(), kb_id)
                    ),
                ),
                result_file_stem=result_stem,
                gen_pdf=True,
                live=Live(console=get_console()),
            )
            print("✓ 研究报告生成完成（Markdown/PDF）")

            # --- Generate PPT based on conversation history ---
            try:
                pptx_stream, output_name = asyncio.run(
                    research_service.ppt_generate(
                        request=PPTGenerateRequest(conversation_id=conv_id)
                    )
                )
                # Ensure output directory exists
                out_dir = os.path.dirname(output_name)
                os.makedirs(out_dir, exist_ok=True)
                # Write PPTX to disk
                with open(output_name, "wb") as f:
                    f.write(pptx_stream.read())
                print(f"✓ PPT 生成完成：{output_name}")
            except Exception:
                logger.exception("✗ PPT 生成失败")
                return 1
            return 0
        except Exception as e:
            logger.exception("✗ 生成失败")
            return 1

    def _handle_qa(self, args: argparse.Namespace) -> int:
        try:
            service = self._get_service()
            
            # 解析多个会议名称和文件路径
            conference_names = [name.strip() for name in args.name.split(',')]
            file_sources = [path.strip() for path in args.files_src.split(',')]
            
            if len(conference_names) != len(file_sources):
                raise ValueError(
                    f"会议名称数量 ({len(conference_names)}) 必须与文件路径数量 ({len(file_sources)}) 一致"
                )
            
            # 解析每个会议并确保文档已入库，获取 kb_ids
            kb_ids = []
            reporter = RichProgressReporter(console=get_console())
            
            for conf_name, file_src in zip(conference_names, file_sources):
                name = conf_name.strip()
                m = re.search(r'(19|20)\d{2}', name)
                if not m:
                    raise ValueError(f'会议名称 "{name}" 中未检测到年份，请包含四位数年份，如 2025')
                
                year = int(m.group(0))
                full_name = name
                base_name_no_year = re.sub(r'[\s\(\[\{,]*' + m.group(0) + r'[\s\)\]\},]*', ' ', name).strip()
                if not base_name_no_year:
                    base_name_no_year = name.replace(m.group(0), '').strip()
                
                short_name = None
                compact = re.sub(r'\s+', '', base_name_no_year)
                if compact.isupper() and 2 <= len(compact) <= 12:
                    short_name = compact
                
                req = ConferenceParseDocsRequest(
                    short_name=short_name,
                    full_name=full_name,
                    year=year,
                    docs_src_dir=file_src,
                )
                
                reporter.info(f"正在解析 {full_name} 的文档，这可能需要一些时间...")
                conf_id, kb_id = asyncio.run(
                    service.ensure_conference_and_ingest_docs(req, reporter=reporter)
                )
                kb_ids.append(kb_id)
            
            from deepinsight.service.research.research import ResearchService
            from deepinsight.service.schemas.research import ResearchRequest, SceneType, ResearchArgs
            from deepinsight.service.schemas.streaming import Message, MessageContent, MessageContentType
            from deepinsight.cli.commands.stream import run_research_and_save_report_sync, make_report_filename
            
            research_service = ResearchService(self._get_config())
            
            # 构建 conversation_id（支持单个或多个会议）
            if len(conference_names) == 1:
                # 单个会议：保持原有格式
                name = conference_names[0]
                m = re.search(r'(19|20)\d{2}', name)
                year = int(m.group(0)) if m else ""
                base_name_no_year = re.sub(r'[\s\(\[\{,]*' + (m.group(0) if m else '') + r'[\s\)\]\},]*', ' ', name).strip()
                short_name = None
                compact = re.sub(r'\s+', '', base_name_no_year)
                if compact.isupper() and 2 <= len(compact) <= 12:
                    short_name = compact
                base = (short_name or base_name_no_year).strip()
                slug = re.sub(r"\s+", "-", base)
                conv_id = f"qa-{slug}-{year}"
                expert_name = f"conference_qa_{base_name_no_year.replace(' ', '_')}_{year}"
            else:
                # 多个会议：使用类似 cross_topic 的格式
                conference_slugs = []
                for conf_name in conference_names:
                    m = re.search(r'(19|20)\d{2}', conf_name)
                    year = int(m.group(0)) if m else ""
                    base = re.sub(r'[\s\(\[\{,]*' + (m.group(0) if m else '') + r'[\s\)\]\},]*', ' ', conf_name).strip()
                    slug = re.sub(r"\s+", "-", base)
                    conference_slugs.append(f"{slug}-{year}")
                conv_id = f"qa-{'-'.join(conference_slugs)}"
                expert_name = f"conference_qa_{'_'.join([name.replace(' ', '_').replace(',', '') for name in conference_names])}"
            
            query = args.question.strip()
            result_stem = make_report_filename(
                question=query,
                expert=expert_name,
            )
            
            run_research_and_save_report_sync(
                service=research_service,
                request=ResearchRequest(
                    conversation_id=conv_id,
                    messages=[
                        Message(
                            content=MessageContent(text=query),
                            content_type=MessageContentType.plain_text,
                        )
                    ],
                    scene_type=SceneType.CONFERENCE_QA,
                    allow_user_clarification=False,
                    allow_edit_research_brief=False,
                    allow_edit_report_outline=False,
                    search_type=["rag_retrieval", "web_search"],
                    args=ResearchArgs(
                        retrieval_options=self._create_retrieval_options(self._get_config(), kb_ids)
                    ),
                ),
                result_file_stem=result_stem,
                gen_pdf=True,
                live=Live(console=get_console()),
            )
            print("✓ 问答完成（Markdown/PDF）")
            return 0
        except Exception as e:
            logger.exception("✗ 问答失败")
            return 1

    def _create_retrieval_options(self, config: Config, kb_ids):
        """
        创建检索选项，支持单个或多个 kb_id
        
        Args:
            config: 配置对象
            kb_ids: 单个 kb_id (int) 或 kb_id 列表 (List[int])
        """
        from deepinsight.service.schemas.research import ResearchArgs, RetrievalArgs, ArgOptionsGeneric
        
        rag_engine = self.get_rag_engine_type(config)
        
        # 统一转换为列表
        if isinstance(kb_ids, int):
            kb_id_list = [kb_ids]
        else:
            kb_id_list = kb_ids
        
        retrieval_options = [
            ArgOptionsGeneric(
                type=rag_engine,
                params=RetrievalArgs(
                    dataset_ids=[str(kb_id) for kb_id in kb_id_list],  # 支持多个
                    top_k=10,
                    top_n=3,
                )
            )
        ]
        return retrieval_options

    def _handle_topic(self, args: argparse.Namespace) -> int:
        """Handle cross-conference topic analysis command."""
        try:
            service = self._get_service()  # ConferenceService
            question = args.question.strip()  # 用户问题，不需要包含会议名称
            
            # Parse conference names and file sources
            conference_names = [name.strip() for name in args.name.split(',')]
            file_sources = [path.strip() for path in args.file_src.split(',')]
            
            if len(conference_names) != len(file_sources):
                raise ValueError(
                    f"会议名称数量 ({len(conference_names)}) 必须与文件路径数量 ({len(file_sources)}) 一致"
                )
            
            # 解析每个会议并确保文档已入库，获取 kb_ids
            kb_ids = []
            reporter = RichProgressReporter(console=get_console())
            
            for conf_name, file_src in zip(conference_names, file_sources):
                # 解析会议名称和年份（复用现有逻辑）
                name = conf_name.strip()
                m = re.search(r'(19|20)\d{2}', name)
                if not m:
                    raise ValueError(f'会议名称 "{name}" 中未检测到年份，请包含四位数年份，如 2025')
                
                year = int(m.group(0))
                full_name = name
                base_name_no_year = re.sub(r'[\s\(\[\{,]*' + m.group(0) + r'[\s\)\]\},]*', ' ', name).strip()
                if not base_name_no_year:
                    base_name_no_year = name.replace(m.group(0), '').strip()
                
                short_name = None
                compact = re.sub(r'\s+', '', base_name_no_year)
                if compact.isupper() and 2 <= len(compact) <= 12:
                    short_name = compact
                
                req = ConferenceParseDocsRequest(
                    short_name=short_name,
                    full_name=full_name,
                    year=year,
                    docs_src_dir=file_src,
                )
                
                reporter.info(f"正在解析 {full_name} 的文档，这可能需要一些时间...")
                conf_id, kb_id = asyncio.run(
                    service.ensure_conference_and_ingest_docs(req, reporter=reporter)
                )
                kb_ids.append(kb_id)
            
            # 使用 ResearchService（类似 _handle_generate，但走跨会议主题分析图）
            from deepinsight.service.research.research import ResearchService
            from deepinsight.service.schemas.research import ResearchRequest, SceneType, ResearchArgs
            from deepinsight.service.schemas.streaming import Message, MessageContent, MessageContentType
            from deepinsight.cli.commands.stream import run_research_and_save_report_sync, make_report_filename
            import os
            
            research_service = ResearchService(self._get_config())
            
            # 构建 conversation_id（包含多个会议信息）
            conference_slugs = []
            for conf_name in conference_names:
                m = re.search(r'(19|20)\d{2}', conf_name)
                year = int(m.group(0)) if m else ""
                base = re.sub(r'[\s\(\[\{,]*' + (m.group(0) if m else '') + r'[\s\)\]\},]*', ' ', conf_name).strip()
                slug = re.sub(r"\s+", "-", base)
                conference_slugs.append(f"{slug}-{year}")
            
            conv_id = f"cross_topic-{'-'.join(conference_slugs)}"
            
            # 为了让跨会议图在不依赖额外参数的情况下也能识别会议信息，
            # 在问题中显式包含会议名称列表（同时仍保持原始 question 的语义）
            conf_names_str = ", ".join(conference_names)
            query = f"{question}（涉及会议：{conf_names_str}）"
            
            # 生成文件名
            result_stem = make_report_filename(
                question=question,
                expert=f"cross_topic_{'_'.join([name.replace(' ', '_').replace(',', '') for name in conference_names])}",
            )
            
            # 调用 ResearchService，使用新的 CROSS_TOPIC_RESEARCH 场景类型，
            # 直接路由到跨会议主题分析图（不经过 conference_qa 的 supervisor / clarify 流程）
            run_research_and_save_report_sync(
                service=research_service,
                request=ResearchRequest(
                    conversation_id=conv_id,
                    messages=[
                        Message(
                            content=MessageContent(text=query),
                            content_type=MessageContentType.plain_text,
                        )
                    ],
                    scene_type=SceneType.CROSS_TOPIC_RESEARCH,
                    allow_user_clarification=False,
                    allow_edit_research_brief=False,
                    allow_edit_report_outline=False,
                    search_type=["rag_retrieval", "web_search"],
                    args=ResearchArgs(
                        retrieval_options=self._create_retrieval_options(
                            self._get_config(), 
                            kb_ids  # 传递多个 kb_ids
                        )
                    ),
                ),
                result_file_stem=result_stem,
                gen_pdf=True,
                live=Live(console=get_console()),
            )
            
            print("✓ 跨会议主题分析完成（Markdown/PDF）")
            return 0
        except Exception as e:
            logger.exception("✗ 跨会议主题分析失败")
            return 1

    def get_rag_engine_type(self, config: Config) -> Optional[str]:
        """Get configured RAG engine type from config.
        
        Returns:
            'lightrag', 'llamaindex', or None if not configured
        """
        try:
            engine_type = config.rag.engine.type
            if engine_type in ['lightrag', 'llamaindex']:
                return engine_type
            return None
        except (AttributeError, KeyError):
            return None