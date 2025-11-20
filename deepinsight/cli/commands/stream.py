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

import asyncio
from copy import deepcopy
from typing import Dict, List, Optional
import os
import re
import sys
from enum import Enum
from datetime import datetime
from urllib.parse import urlparse

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession, HTML
from prompt_toolkit.validation import Validator

from deepinsight.service.research.research import ResearchService
from deepinsight.config.config import CONFIG, load_config
from deepinsight.service.schemas.research import ResearchRequest, SceneType
from deepinsight.utils.trans_md_to_pdf import save_markdown_as_pdf
from deepinsight.service.schemas.streaming import (
    EventType,
    MessageToolCallContent,
    MessageContentType,
)

# ANSI escape helpers
CSI = "\x1b["
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
GRAY = "\x1b[90m"

report_steps = ["需求澄清", "思路生成", "深度搜索", "大纲生成", "报告生成"]


class REPORT_STEPS(Enum):
    CLARIFY = 0
    BRIEF = 1
    DEEP_SEARCH = 2
    OUTLINE_GENERATION = 3
    REPORT_GENERATION = 4
    FINISH = 5


# --- Moved from report_io.py ---
DEFAULT_OUTPUT_DIR = "./reports"


class Progress:
    def __init__(self, steps, show_status=True, title=None):
        """
        steps: list of step names
        show_status: whether to show Done/Doing/Pending labels
        title: optional title printed above each block
        """
        self.steps = list(steps)
        self.n = len(self.steps)
        self.current = 0
        self.show_status = show_status
        self.title = title

        # Try to enable ANSI on Windows consoles (best-effort)
        if os.name == "nt":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                handle = kernel32.GetStdHandle(-11)
                mode = ctypes.c_uint()
                kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            except Exception:
                # If it fails, colored output may not show; that's OK.
                pass

    def _format_line(self, idx):
        name = self.steps[idx]
        if idx < self.current:
            status = f"{GREEN}✔{RESET}"
            label = f"{GREEN}{BOLD} Done{RESET}" if self.show_status else ""
            return f"  {status} {name}{label}"
        elif idx == self.current:
            arrow = "➡"
            status = f"{YELLOW}●{RESET}"
            label = f"{YELLOW}{BOLD} Doing{RESET}" if self.show_status else ""
            return f"{arrow} {status} {BOLD}{name}{RESET}{label}"
        else:
            status = f"{GRAY}·{RESET}"
            label = f" {DIM}Pending{RESET}" if self.show_status else ""
            return f"  {status} {DIM}{name}{RESET}{label}"

    def print_block(self):
        """普通打印，不覆盖之前内容。"""
        # optional title
        if self.title:
            print(f"{CYAN}{BOLD}{self.title}{RESET}")
        print()  # spacing above block
        for i in range(self.n):
            print(self._format_line(i))
        # separator to visually separate blocks
        print("-" * 40)
        sys.stdout.flush()

    def set_step(self, idx):
        """设置当前步骤并打印（不会覆盖旧内容）。"""
        if idx < 0:
            idx = 0
        if idx >= self.n:
            idx = self.n - 1
        self.current = idx
        self.print_block()

    def next(self):
        if self.current < self.n - 1:
            self.current += 1
        self.print_block()

    def prev(self):
        if self.current > 0:
            self.current -= 1
        self.print_block()

progress_show = Progress(report_steps)


def sanitize_filename(s: str) -> str:
    """移除或替换掉文件名中的非法字符"""
    return re.sub(r'[\\/*?:"<>| ]', "_", s)


def make_report_filename(question: str, expert: str, output_dir: str = DEFAULT_OUTPUT_DIR) -> str:
    prefix = sanitize_filename(question[:10])
    expert_clean = sanitize_filename(expert)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = "_".join([prefix, expert_clean, timestamp])
    return filename


def _get_workspace_root() -> str:
    cfg = CONFIG
    if cfg is None:
        cfg_path = os.getenv('DEEPINSIGHT_CONFIG', os.path.join(os.getcwd(), 'config.yaml'))
        try:
            cfg = load_config(cfg_path)
        except Exception:
            cfg = None
    if cfg and getattr(cfg, 'workspace', None):
        return os.path.abspath(cfg.workspace.work_root)
    return os.getcwd()


def get_with_md_file_name(origin_name: str, conversation_id: str, output_folder_name: str = "conference_report_result"):
    """Return Markdown path directly under the conversation root directory."""
    base_name = os.path.basename(origin_name)
    work_root = _get_workspace_root()
    convo_dir = os.path.join(work_root, output_folder_name, conversation_id)
    os.makedirs(convo_dir, exist_ok=True)
    return os.path.join(convo_dir, base_name + ".md")


def get_with_pdf_file_name(origin_name: str, conversation_id: str, output_folder_name: str = "conference_report_result"):
    """Return PDF path directly under the conversation root directory."""
    base_name = os.path.basename(origin_name)
    work_root = _get_workspace_root()
    convo_dir = os.path.join(work_root, output_folder_name, conversation_id)
    os.makedirs(convo_dir, exist_ok=True)
    return os.path.join(convo_dir, base_name + ".pdf")


def write_result(
    final_text: str,
    result_file_stem: str,
    conversation_id: str,
    gen_pdf: bool = True,
    console: Optional[Console] = None,
    success_message: str = "✅ 报告已成功保存至：{result_file}",
    output_folder_name: str = "conference_report_result",
) -> None:
    """将 Markdown 写入到固定目录，并可选生成 PDF。"""
    md_file_name = get_with_md_file_name(result_file_stem, conversation_id, output_folder_name)
    with open(md_file_name, "w", encoding="utf-8") as f:
        f.write(final_text)

    if console is not None and success_message:
        console.print(
            f"[bold green]{success_message.format(result_file=md_file_name)}[/bold green]"
        )

    if gen_pdf:
        pdf_file_name = get_with_pdf_file_name(result_file_stem, conversation_id, output_folder_name)
        try:
            # 为相对路径图片（如 charts/xxx.png）提供解析根目录
            from os.path import dirname
            base_url = dirname(md_file_name)
            save_markdown_as_pdf(markdown_content=final_text, output_filename=pdf_file_name, base_url=base_url)
            if console is not None and success_message:
                console.print(
                    f"[bold green]PDF {success_message.format(result_file=pdf_file_name)}[/bold green]"
                )
        except Exception as e:
            if console is not None:
                console.print(f"[yellow]⚠️ 生成 PDF 失败：{e}[/yellow]")


# --- Moved from url.py ---
def is_internal_url(url: str) -> bool:
    """判断是否是内网地址（包含 huawei 或 IP 地址）"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # 规则1: 包含 huawei
    if "huawei" in hostname.lower():
        return True

    # 规则2: IP 地址（v4/v6）
    ip_pattern = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^\[?[0-9a-fA-F:]+\]?$")
    if ip_pattern.match(hostname):
        return True

    return False


def extract_content_from_url(url: str) -> Optional[str]:
    """根据URL选择提取方式（内网mock / 外网 Tavily）"""
    if is_internal_url(url):
        # 使用传入的 console 更合适，但此函数独立时简化输出
        return f"（内网解析结果）这是从内网地址 {url} 抓取的内容。"
    else:
        try:
            from tavily import TavilyClient  # type: ignore
            client = TavilyClient()
            response = client.extract(urls=[url])
            if isinstance(response, dict) and "results" in response and len(response["results"]) > 0:
                return response["results"][0].get("raw_content")
        except Exception:
            return None
        return None


async def run_research_and_save_report(
    service: ResearchService,
    request: ResearchRequest,
    result_file_stem: str,
    *,
    gen_pdf: bool = True,
    live: Optional[Live] = None,
) -> str:
    with live or Live(refresh_per_second=4, vertical_overflow="ellipsis") as live:
        await _process_request(service, request, live, result_file_stem, gen_pdf)

def build_prompt_message(header: str) -> HTML:
    return HTML(
        f"\n\n➡️ <b><ansiyellow>{header}</ansiyellow></b> > \n\n"
        "<ansiblue>👉 编辑完成后，请按 </ansiblue>"
        "<ansigreen><b>Esc</b></ansigreen>"
        "<ansiblue> 然后 </ansiblue>"
        "<ansigreen><b>Enter</b></ansigreen>"
        "<ansiblue> 提交。</ansiblue>\n\n"
    )

def construct_default_user_clarification(text: str) -> str:
    # 默认值
    defaults = {
        "用户": "技术团队",
        "目的": "技术分析",
        "范围": "全方位分析",
    }

    result_map = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # 只处理包含问号的行
        if "？" in line or "?" in line:
            parts = re.split(r"[？?]", line, maxsplit=1)
            if len(parts) == 2:
                question = parts[0].strip()
                default_answer = ""
                for key in defaults.keys():
                    if key in question:
                        default_answer = defaults[key]
                        break
                result_map.append(f"【{question}】{default_answer}")

    return "\n".join(result_map)

async def ask_user(prompt_text: str, mode: EventType, live: Live) -> str:
    session = PromptSession(
        multiline=True,
        validator=non_empty_validator(),
        validate_while_typing=False,
    )
    if mode == EventType.interrupt_clarification or mode == EventType.interrupt:
        progress_show.set_step(REPORT_STEPS.CLARIFY.value)
        live.console.print(f"\n💡 请回答如下问题：\n", style="bold yellow")
        live.console.print(Markdown(prompt_text), style="cyan")
        user_input = await session.prompt_async(
            build_prompt_message(header="请输入您的回答"),
            default=construct_default_user_clarification(prompt_text),
        )
        return user_input

    elif mode == EventType.interrupt_execute_plan_edit or mode == EventType.interrupt_report_outline_edit:
        tips = "分析思路如下" if mode == EventType.interrupt_execute_plan_edit else "报告大纲如下"
        if mode == EventType.interrupt_execute_plan_edit:
            progress_show.set_step(REPORT_STEPS.BRIEF.value)
        else:
            progress_show.set_step(REPORT_STEPS.OUTLINE_GENERATION.value)
        user_input = await session.prompt_async(
            build_prompt_message(header=tips),
            default=prompt_text,
        )
        progress_show.set_step(REPORT_STEPS.DEEP_SEARCH.value)
        return user_input

    else:
        raise ValueError(f"未知的交互模式: {mode}")


async def _process_request(service: ResearchService, request: ResearchRequest, live: Live, result_file_stem: str,
                           gen_pdf=True):
    accumulated_texts = {}
    accumulated_tool_calls: Dict[str, List[MessageToolCallContent]] = {}  # Message id -> tool call list
    is_gen_report = False
    agen = service.chat(request=request)
    try:
        async for stream_event in agen:
            if stream_event.event == EventType.thinking_message_chunk:
                for msg in stream_event.messages:
                    # if msg.content_type == ResponseMessageContentType.plain_text and msg.content.text:
                    if msg.content_type == MessageContentType.plain_text:
                        msg_id = msg.id or str(uuid.uuid4())
                        if msg_id not in accumulated_texts:
                            accumulated_texts[msg_id] = msg.content.text or ""
                            live.update("")
                            live.console.print(f"[bold blue]💬 正在接收消息流，请稍候...[/bold blue]")
                        chunk_text = msg.content.text or ""
                        # 处理占位符，避免整条消息被隐藏
                        if chunk_text.startswith("[][][]"):
                            chunk_text = chunk_text[len("[][][]"):]
                        if accumulated_texts[msg_id].startswith("[][][]"):
                            accumulated_texts[msg_id] = accumulated_texts[msg_id][len("[][][]"):]
                        accumulated_texts[msg_id] += chunk_text
                        text = Text(accumulated_texts[msg_id], style="cyan")
                        panel = Panel(text, title=f"Message", border_style="blue", expand=True)
                        live.update(panel)

            elif stream_event.event == EventType.thinking_step_topic:
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.plain_text:
                        msg_id = msg.id or str(uuid.uuid4())
                        if msg_id not in accumulated_texts:
                            accumulated_texts[msg_id] = ""
                            live.update("")
                            live.console.print(f"[bold blue]🧭 正在梳理阶段主题...[/bold blue]")
                        chunk_text = msg.content.text or ""
                        if chunk_text.startswith("[][][]"):
                            chunk_text = chunk_text[len("[][][]"):]
                        if accumulated_texts[msg_id].startswith("[][][]"):
                            accumulated_texts[msg_id] = accumulated_texts[msg_id][len("[][][]"):]
                        accumulated_texts[msg_id] += chunk_text
                        text = Text(accumulated_texts[msg_id], style="cyan")
                        panel = Panel(text, title="阶段主题", border_style="blue", expand=True)
                        live.update(panel)

            elif stream_event.event == EventType.thinking_report_outline_generating:
                progress_show.set_step(REPORT_STEPS.OUTLINE_GENERATION.value)
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.plain_text:
                        msg_id = msg.id or str(uuid.uuid4())
                        if msg_id not in accumulated_texts:
                            accumulated_texts[msg_id] = ""
                            live.update("")
                            live.console.print(f"[bold blue]📑 正在生成报告大纲...[/bold blue]")
                        chunk_text = msg.content.text or ""
                        if chunk_text.startswith("[][][]"):
                            chunk_text = chunk_text[len("[][][]"):]
                        if accumulated_texts[msg_id].startswith("[][][]"):
                            accumulated_texts[msg_id] = accumulated_texts[msg_id][len("[][][]"):]
                        accumulated_texts[msg_id] += chunk_text
                        text = Text(accumulated_texts[msg_id], style="cyan")
                        panel = Panel(text, title="大纲生成中", border_style="blue", expand=True)
                        live.update(panel)

            elif stream_event.event == EventType.report_chunk:
                progress_show.set_step(REPORT_STEPS.REPORT_GENERATION.value)
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.plain_text:
                        msg_id = msg.id or str(uuid.uuid4())
                        if msg_id not in accumulated_texts:
                            accumulated_texts[msg_id] = ""
                            live.update("")
                            live.console.print(f"[bold blue]📝 正在生成报告内容...[/bold blue]")
                        chunk_text = msg.content.text or ""
                        if chunk_text.startswith("[][][]"):
                            chunk_text = chunk_text[len("[][][]"):]
                        if accumulated_texts[msg_id].startswith("[][][]"):
                            accumulated_texts[msg_id] = accumulated_texts[msg_id][len("[][][]"):]
                        accumulated_texts[msg_id] += chunk_text
                        text = Text(accumulated_texts[msg_id], style="cyan")
                        panel = Panel(text, title="报告生成中", border_style="blue", expand=True)
                        live.update(panel)

            elif stream_event.event == EventType.message_chunk:
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.plain_text:
                        msg_id = msg.id or str(uuid.uuid4())
                        if msg_id not in accumulated_texts:
                            accumulated_texts[msg_id] = ""
                            live.update("")
                            live.console.print(f"[bold blue]💬 正在接收消息流，请稍候...[/bold blue]")
                        chunk_text = msg.content.text or ""
                        if chunk_text.startswith("[][][]"):
                            chunk_text = chunk_text[len("[][][]"):]
                        if accumulated_texts[msg_id].startswith("[][][]"):
                            accumulated_texts[msg_id] = accumulated_texts[msg_id][len("[][][]"):]
                        accumulated_texts[msg_id] += chunk_text
                        text = Text(accumulated_texts[msg_id], style="cyan")
                        panel = Panel(text, title="Message", border_style="blue", expand=True)
                        live.update(panel)

            elif stream_event.event == EventType.thinking_tool_calls:
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.tool_call:
                        tool_calls = msg.content.tool_calls
                        if msg.id not in accumulated_tool_calls:
                            accumulated_tool_calls[msg.id] = []

                        for tool_call_item in tool_calls:
                            index = tool_call_item.index
                            while len(accumulated_tool_calls[msg.id]) <= index:
                                live.update(f"")
                                accumulated_tool_calls[msg.id].append(
                                    MessageToolCallContent(
                                        id="",
                                        name="",
                                        args="",
                                        result="",
                                    )
                                )
                                live.console.print(
                                    f"[bold blue]⚙️ 正在执行工具 {tool_call_item.name}...[/bold blue]"
                                )

                            acc_call = accumulated_tool_calls[msg.id][index]
                            acc_call.id += tool_call_item.id or ""
                            acc_call.name += tool_call_item.name or ""
                            acc_call.args += tool_call_item.args or ""
                            acc_call.result += tool_call_item.result or ""

            elif stream_event.event == EventType.thinking_tool_calls_result:
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.tool_call and msg.content.tool_calls:
                        tool_calls = msg.content.tool_calls
                        for tool_call in tool_calls:
                            find_tool_call = None
                            for msg_id, message_tool_calls in accumulated_tool_calls.items():
                                for each in message_tool_calls:
                                    if each.id == tool_call.id:
                                        each.result = tool_call.result
                                        find_tool_call = each
                                        break
                            live.update("")
                            live.console.print(
                                f"[bold blue]✅ 工具 {find_tool_call.name if find_tool_call else tool_call.name} 执行完成[/bold blue]"
                            )

            elif stream_event.event == EventType.final_report:
                if not is_gen_report:
                    progress_show.set_step(REPORT_STEPS.REPORT_GENERATION.value)
                    is_gen_report = True

                final_text = ""
                for msg in stream_event.messages:
                    if msg.content_type == MessageContentType.plain_text and msg.content.text:
                        final_text += msg.content.text

                live.update("")
                live.console.print(
                    Panel(final_text, title="Final Report", border_style="green", expand=True)
                )
                folder_name = "research_result" if request.scene_type == SceneType.DEEP_RESEARCH else "conference_report_result"
                write_result(
                    final_text=final_text,
                    result_file_stem=result_file_stem,
                    conversation_id=request.conversation_id,
                    gen_pdf=gen_pdf,
                    console=live.console,
                    success_message="[bold green]✅ 报告已成功保存至：[/bold green][yellow]{result_file}[/yellow]",
                    output_folder_name=folder_name,
                )

            elif stream_event.event.startswith(EventType.interrupt):
                prompt_text = "\n".join(
                    [msg.content.text for msg in stream_event.messages if msg.content.text]
                )
                live.update("")
                live.stop()
                user_input = await ask_user(prompt_text=prompt_text, mode=stream_event.event, live=live)
                new_request = deepcopy(request)
                new_request.query = user_input
                try:
                    await agen.aclose()
                except Exception:
                    pass
                return await run_research_and_save_report(
                    service=service,
                    request=new_request,
                    result_file_stem=result_file_stem,
                    gen_pdf=gen_pdf,
                    live=None,
                )
    except Exception as e:
        live.console.print(f"[red]Error during chat: {e}[/red]")
        raise e
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass

    live.console.print()  # newline after each request
    return None


def run_research_and_save_report_sync(
    service: ResearchService,
    request: ResearchRequest,
    result_file_stem: str,
    *,
    gen_pdf: bool = True,
    live: Optional[Live] = None,
) -> str:
    """同步包装器，便于在非 async 的 CLI 命令中调用。"""
    return asyncio.run(
        run_research_and_save_report(
            service=service,
            request=request,
            result_file_stem=result_file_stem,
            gen_pdf=gen_pdf,
            live=live,
        )
    )
    
def non_empty_validator():
    return Validator.from_callable(
        lambda text: bool(text.strip()),
        error_message="Input cannot be empty",
        move_cursor_to_end=True,
    )