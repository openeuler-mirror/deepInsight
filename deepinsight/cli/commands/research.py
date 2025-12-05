import argparse
import sys
import os
import re
import asyncio
from datetime import datetime
from typing import Optional
from rich.live import Live
from rich.panel import Panel
from rich.markdown import Markdown
from InquirerPy import inquirer
from typing import List

from deepinsight.config.config import load_config
from deepinsight.config.config import Config
from deepinsight.service.research.research import ResearchService
from deepinsight.service.schemas.research import ResearchRequest, SceneType
from deepinsight.service.schemas.streaming import Message, MessageContent, MessageContentType
from deepinsight.core.types.graph_config import SearchAPI
from deepinsight.cli.commands.stream import (
    run_research_and_save_report_sync,
    run_research_and_save_report,
    extract_content_from_url,
    make_report_filename,
    sanitize_filename,
    write_result,
    get_with_md_file_name,
    DEFAULT_OUTPUT_DIR,
)
from deepinsight.core.utils.research_utils import load_expert_config
from deepinsight.core.agent.expert_review.expert_review import build_expert_review_graph
from deepinsight.utils.llm_utils import init_langchain_models_from_llm_config
from deepinsight.core.prompt.prompt_manager import PromptManager
from deepinsight.core.types.graph_config import ExpertDef
from langchain_core.messages import SystemMessage


class ResearchCommand:
    def __init__(self):
        self.version = "1.0.0"

    def execute(self, args: argparse.Namespace) -> int:
        parser = self._create_parser()
        research_args = parser.parse_args(sys.argv[2:])
        if research_args.subcommand == 'start':
            return self._handle_start_command(research_args)
        parser.print_help()
        return 1

    def _create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog='deepinsight research',
            description='Deep Research Assistant - AI-powered research tool'
        )
        subparsers = parser.add_subparsers(dest='subcommand', help='Operations')

        start_parser = subparsers.add_parser('start', help='Run research')
        start_parser.add_argument('--topic', '-t', type=str, required=False, help='Research topic or URL')
        return parser

    def _handle_start_command(self, args: argparse.Namespace) -> int:
        cfg_path = os.getenv('DEEPINSIGHT_CONFIG', os.path.join(os.getcwd(), 'config.yaml'))
        config = load_config(cfg_path)
        return run_insight(config=config, gen_pdf=True, initial_topic=args.topic)

def select_with_live_pause(live: Live | None, **kwargs):
    if live:
        live.stop()
    try:
        return inquirer.select(**kwargs).execute()
    finally:
        if live:
            live.start()


def checkbox_with_live_pause(live: Live | None, **kwargs):
    if live:
        live.stop()
    try:
        return inquirer.checkbox(**kwargs).execute()
    finally:
        if live:
            live.start()


def choose_expert(require_one: bool = False, expert_type: str = "writer", live: Live | None = None) -> List[str]:
    experts = load_expert_config("./experts.yaml")
    choices = [e.prompt_key for e in experts if getattr(e, "type", "") == expert_type]
    if not choices:
        return []
    if require_one:
        selected = checkbox_with_live_pause(
            live,
            message="请选择专家（至少选择一个）",
            choices=choices,
            instruction="空格选择，回车确认",
            pointer="➤ ",
        )
        return selected or choices
    selected = checkbox_with_live_pause(
        live,
        message="请选择专家（可多选）",
        choices=choices,
        instruction="空格选择，回车确认",
        pointer="➤ ",
    )
    return selected or []

def run_generate_report(
    question: str,
    insight_service: ResearchService,
    scene_type: str,
    search_types: List[SearchAPI],
    output_dir: str,
    conversation_id: str,
    live: Live,
    gen_pdf: bool = True,
) -> str:
    expert_names = choose_expert(require_one=False, expert_type="writer", live=live)
    def create_one_generate(expert_name):
        return ResearchRequest(
            conversation_id=conversation_id,
            messages=[
                Message(
                    content=MessageContent(text=question),
                    content_type=MessageContentType.plain_text,
                )
            ],
            scene_type=SceneType.DEEP_RESEARCH,
            search_api=search_types,
            expert_review_enable=False,
            expert_name=expert_name,
        )
    if not expert_names:
        sub_file_name = make_report_filename(question=question, expert="", output_dir=output_dir)
        request = create_one_generate(expert_name=None)
        run_research_and_save_report_sync(
            service=insight_service,
            request=request,
            result_file_stem=sub_file_name,
            gen_pdf=gen_pdf,
            live=live,
        )
        return sub_file_name
    else:
        report_filenames: List[str] = []
        for index, expert_name in enumerate(expert_names):
            sub_file_name = make_report_filename(question=question, expert=expert_name, output_dir=output_dir)
            report_filenames.append(sub_file_name)
            request = create_one_generate(expert_name=expert_name)
            run_research_and_save_report_sync(
                service=insight_service,
                request=request,
                result_file_stem=sub_file_name,
                gen_pdf=gen_pdf,
                live=live,
            )
            live.console.print(f"[bold green]✅ 专家 [yellow]{expert_name}[/yellow] 报告已生成。[/bold green] \n\n")
            left_experts = expert_names[index:]
            if len(left_experts) > 1:
                live.console.print(f"[bold green]✅ 后续继续由专家 [yellow]{','.join(left_experts[1:])}[/yellow] 生成报告。[/bold green] \n\n")
            if len(expert_names) > 1 and index == len(expert_names) - 1:
                live.console.print(f"[bold green]✅ 专家 [yellow]{','.join(expert_names)}[/yellow] 报告均已生成，正在总结最终报告。[/bold green] \n\n")
        if len(expert_names) == 1:
            return report_filenames[0]
        all_sub_reports = []
        for each in report_filenames:
            with open(get_with_md_file_name(each, conversation_id, "research_result"), "r", encoding="utf-8") as f:
                all_sub_reports.append(f.read())
        models, default_model = init_langchain_models_from_llm_config(insight_service.get_default_config())
        summary_prompt = (
            PromptManager(insight_service.config.prompt_management)
            .get_prompt(name="summary_prompt", group="summary_experts")
            .format(report="\n\n".join(all_sub_reports))
        )
        summary_file_name = make_report_filename(question=question, expert="summary", output_dir=output_dir)
        response = default_model.invoke([SystemMessage(content=summary_prompt)])
        write_result(
            final_text=response.content,
            result_file_stem=summary_file_name,
            conversation_id=conversation_id,
            gen_pdf=gen_pdf,
            console=live.console,
            success_message="[bold green]✅ 专家汇总报告已成功保存至：[/bold green][yellow]{result_file}[/yellow]",
            output_folder_name="research_result",
        )
        return summary_file_name

def save_expert_reviews(result: dict, output_file: str, conversation_id: str, live: Live):
    markdown_parts = []
    for expert_name, comment in result["expert_comments"].items():
        markdown_parts.append(f"### 👨‍💼 {expert_name}\n\n{comment.strip()}\n")
    final_markdown = "\n\n".join(markdown_parts)
    live.console.print(
        Panel(
            Markdown(final_markdown),
            title="[bold green]📑 专家点评结果如下：[/bold green]",
            border_style="green",
        )
    )
    write_result(
        final_text=final_markdown,
        result_file_stem=output_file,
        conversation_id=conversation_id,
        gen_pdf=True,
        console=live.console,
        success_message="[bold green]✅ 专家点评结果已保存至：[/bold green][yellow]{result_file}[/yellow]",
        output_folder_name="research_result",
    )

def run_expert_review(question: str, insight_service: ResearchService, conversation_id: str, report_file_name: str | None = None, output_dir: str = "", live: Live | None = None):
    origin_question = question
    if report_file_name:
        action = select_with_live_pause(
            live,
            message=f"是否要对该报告进行专家点评？",
            choices=[
                "✅ 是的，对报告进行点评",
                "❌ 否，结束当前流程",
            ],
            default="✅ 是的，对报告进行点评",
            long_instruction="↑/↓ 切换 | 回车确认",
            pointer="➤ ",
        )
        if not action == "✅ 是的，对报告进行点评":
            if live:
                live.console.print("[yellow]⚡ 已跳过专家点评流程[/yellow]")
            return
        else:
            real_name = get_with_md_file_name(report_file_name, conversation_id, "research_result")
            if live:
                live.console.print(f"[green]📄 将对报告 {real_name} 进行专家点评...[/green]")
            with open(real_name, "r", encoding="utf-8") as f:
                question = f.read()
    expert_names = choose_expert(require_one=True, expert_type="reviewer", live=live)
    models, default_model = init_langchain_models_from_llm_config(insight_service.get_default_config())
    export_review_subgraph = build_expert_review_graph(
        [ExpertDef(name=each, prompt_key=each, type="reviewer") for each in expert_names]
    )
    result = asyncio.run(
        export_review_subgraph.ainvoke(
            dict(final_report=question),
            config=dict(
                configurable=dict(
                    prompt_manager=PromptManager(insight_service.config.prompt_management),
                    models=models,
                    default_model=default_model,
                )
            ),
        )
    )
    output_file = make_report_filename(question=origin_question, expert="expert_review", output_dir=output_dir)
    save_expert_reviews(
        result=result,
        output_file=output_file,
        conversation_id=conversation_id,
        live=live or Live(),
    )

def run_insight(config: Config, gen_pdf: bool = True, initial_topic: str | None = None) -> int:
    insight_service = ResearchService(config)
    with Live(refresh_per_second=4, vertical_overflow="ellipsis") as live:
        live.console.print("[bold green]✅ DeepInsight CLI 已成功启动！输入 'exit' 或 'quit' 可退出程序。[/bold green]")
        scene_type = "deep_research"
        search_types = [SearchAPI.TAVILY]
        question = (initial_topic or input("💡 请输入洞察任务的问题或一个URL（按回车确认）：")).encode("utf-8", errors="ignore").decode("utf-8")
        if question.lower().strip() in {"exit", "quit"}:
            live.console.print("[yellow]⚡ 正在退出 DeepInsight CLI，请稍候...[/yellow]")
            return 0
        if question.startswith("http://") or question.startswith("https://"):
            extracted_content = extract_content_from_url(question)
            live.console.print(
                Panel(
                    Markdown(extracted_content[:500] + "...")
                    if extracted_content and len(extracted_content) > 500
                    else Markdown(extracted_content or ""),
                    title="[bold green]✅ 你输入的URL提取内容结果：[/bold green]",
                )
            )
            if not extracted_content:
                live.console.print("[red]❌ 未能成功提取该 URL 的内容，请检查输入或尝试另一个地址。[/red]")
                return 1
            question = extracted_content
        else:
            live.console.print(Panel(question, title="[cyan]🙋 你输入的任务问题如下：[/cyan]"))
        action_mode = select_with_live_pause(
            live,
            message="请选择任务模式：",
            choices=[
                "📄 报告模式",
                "👨‍🏫 点评模式",
            ],
            default="📄 报告模式",
            long_instruction="↑/↓ 切换 | 回车确认",
            pointer="➤ ",
        )
        output_dir = ""
        conversation_id = f"cli-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if action_mode == "📄 报告模式":
            report_file_name = run_generate_report(
                question=question,
                insight_service=insight_service,
                scene_type=scene_type,
                search_types=search_types,
                output_dir=output_dir,
                conversation_id=conversation_id,
                live=live,
                gen_pdf=gen_pdf,
            )
            run_expert_review(
                question=question,
                insight_service=insight_service,
                report_file_name=report_file_name,
                output_dir=output_dir,
                conversation_id=conversation_id,
                live=live,
            )
        else:
            run_expert_review(
                question=question,
                insight_service=insight_service,
                output_dir=output_dir,
                conversation_id=conversation_id,
                live=live,
            )
        return 0
