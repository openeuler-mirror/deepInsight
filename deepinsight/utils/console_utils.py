# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import sys
from typing import Any
from typing import Generator

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich.theme import Theme

from deepinsight.core.messages import Message, StartMessage, ChunkMessage, \
    EndMessage, ErrorMessage, HeartbeatMessage, CompleteMessage

custom_theme = Theme({
    "stream_start": "bold blue",
    "stream_data": "green",
    "stream_chunk": "yellow",
    "stream_end": "bold green",
    "stream_error": "bold red",
    "non_stream": "yellow",
    "heartbeat": "dim cyan",
    "control": "magenta",
})


def display_stream(generator: Generator[Message, None, Any]):
    """
    Display streaming messages in a rich CLI interface with real-time updates.

    Args:
        generator: A generator yielding Message objects of different types

    Returns:
        Any: The final result when generator is exhausted

    Features:
        - Real-time progress tracking
        - Color-coded message display
        - Accumulated data view
        - Error handling
        - Heartbeat monitoring
    """
    console = Console(theme=custom_theme)

    # 初始化进度条
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,  # 完成后自动隐藏
    )

    task_id = progress.add_task("[blue]Processing research...", total=None)

    # 用于累积流数据
    accumulated_data = ""

    with Live(progress, refresh_per_second=10, console=console):
        try:
            while True:
                message = next(generator)
                if isinstance(message, StartMessage):
                    accumulated_data = ""
                    progress.update(task_id, description=f"[blue]Starting research: {message.payload}")
                    console.print(
                        Panel.fit(
                            f"[stream_start]Research started: {message.payload}[/]",
                            title="Stream Start",
                            border_style="stream_start",
                        )
                    )

                elif isinstance(message, ChunkMessage):
                    # 累积数据并实时更新
                    accumulated_data += str(message.payload)
                    progress.update(task_id, description="[green]Receiving data...")

                    # 打印最新数据
                    console.print(
                        Text(str(message.payload), style="stream_data"),
                        end=""
                    )
                    sys.stdout.flush()  # 确保立即输出

                elif isinstance(message, EndMessage):
                    progress.update(task_id, description="[green]Research completed!")
                    console.print(
                        Panel.fit(
                            f"[stream_chunk]{accumulated_data}[/]",
                            title="Stream chunk",
                            border_style="stream_chunk",
                        )
                    )
                    console.print(
                        Panel.fit(
                            f"[stream_end]Research completed: {message.payload}[/]\n\n"
                            f"[dim]Total data received: {len(accumulated_data)} characters",
                            title="Stream End",
                            border_style="stream_end",
                        )
                    )
                    progress.stop()

                elif isinstance(message, ErrorMessage):
                    progress.update(task_id, description="[red]Research failed!")
                    console.print(
                        Panel.fit(
                            f"[stream_error]Error {message.error_code}: {message.error_message}[/]",
                            title="Stream Error",
                            border_style="stream_error",
                        )
                    )
                    progress.stop()

                elif isinstance(message, HeartbeatMessage):
                    latency_info = f" (latency: {message.latency_ms}ms)" if message.latency_ms else ""
                    progress.update(task_id, description=f"[cyan]Heartbeat received{latency_info}")

                elif isinstance(message, CompleteMessage):
                    console.print(
                        Panel.fit(
                            f"[non_stream]{message.payload}[/]",
                            title="Non-stream Response",
                            border_style="non_stream",
                        )
                    )
        except StopIteration as e:
            result = e.value
    return result
