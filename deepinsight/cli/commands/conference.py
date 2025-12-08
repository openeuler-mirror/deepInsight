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

from deepinsight.service.research.research import ResearchService
from deepinsight.service.schemas.research import ResearchRequest, SceneType, PPTGenerateRequest, ResearchArgs, RetrievalArgs, ArgOptionsGeneric
from deepinsight.service.schemas.streaming import Message, MessageContent, MessageContentType
from deepinsight.cli.commands.stream import run_research_and_save_report_sync, make_report_filename
from deepinsight.core.types.graph_config import SearchAPI


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
        elif conf_args.subcommand == 'generate':
            return self._handle_generate(conf_args)
        elif conf_args.subcommand == 'qa':
            return self._handle_qa(conf_args)
        else:
            parser.print_help()
            return 1

    def _create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog='deepinsight conference',
            description='Conference information management (list/remove/generate/qa)',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='''\
Examples:
  deepinsight conference list
  deepinsight conference generate --name "ICLR 2025" --files-src ./docs
  deepinsight conference qa --name "ICLR 2025" --files-src ./docs --question "今年最佳论文有哪些创新点？"
  deepinsight conference remove --id 12
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

        # generate
        generate_parser = subparsers.add_parser('generate', help='Generate conference knowledge base (auto rollback on failure)')
        # Short aliases and renamed source flag: -n for --name, -f for --files-src
        # Note: --files-src replaces the previous --docs-src for clarity.
        generate_parser.add_argument('--name', '-n', required=True, help='Conference name including year, e.g., "ICLR 2025"')
        generate_parser.add_argument('--files-src', '-f', required=True, help='User-provided source directory of files to ingest')
        qa_parser = subparsers.add_parser('qa', help='Conference QA based on ingested documents')
        qa_parser.add_argument('--name', '-n', required=True, help='Conference name including year, e.g., "ICLR 2025"')
        qa_parser.add_argument('--files-src', '-f', required=True, help='User-provided source directory of files to ingest')
        qa_parser.add_argument('--question', '-q', required=True, help='User question to answer against the conference knowledge base')
        return parser

    def _get_config(self):
        if self._config is None:
            cfg_path = os.getenv('DEEPINSIGHT_CONFIG', os.path.join(os.getcwd(), 'config.yaml'))
            self._config = load_config(cfg_path)
        return self._config

    def _get_service(self) -> ConferenceService:
        if self._service is None:
            config = self._get_config()
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
            name = args.name.strip()
            m = re.search(r'(19|20)\d{2}', name)
            if not m:
                raise ValueError('No year detected in name, please include a four-digit year like 2025')
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
                docs_src_dir=args.files_src,
            )
            reporter = RichProgressReporter(console=get_console())
            reporter.info("Parsing documents. This may take a while...")
            conf_id, kb_id = asyncio.run(service.ensure_conference_and_ingest_docs(req, reporter=reporter))
            research_service = ResearchService(self._get_config())
            base = (short_name or full_name).strip()
            slug = re.sub(r"\s+", "-", base)
            conv_id = f"qa-{slug}-{year}"
            query = args.question.strip()
            # 生成带有问题前缀与时间戳的唯一文件名，避免同会议不同问题的报告相互覆盖
            result_stem = make_report_filename(
                question=query,
                expert=f"conference_qa_{(short_name or base_name_no_year).strip()}_{year}",
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
                    search_type=["rag_retrieval", "web_search"],  # Default: RAG + web search
                    args=ResearchArgs(
                        retrieval_options=self._create_retrieval_options(self._get_config(), kb_id)
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

    def _create_retrieval_options(self, config: Config, kb_id: int):
        rag_engine = self.get_rag_engine_type(config)
        retrieval_options = [
            ArgOptionsGeneric(
                type=rag_engine,  # Use configured engine type
                params=RetrievalArgs(
                    dataset_ids=[str(kb_id)],
                    top_k=10,
                    top_n=3,
                )
            )
        ]
        return retrieval_options

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