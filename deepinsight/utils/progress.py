from __future__ import annotations

from typing import Optional, Protocol

from rich.console import Console
from rich import get_console
from rich.progress import Progress, TaskID, BarColumn, TimeRemainingColumn, TextColumn


class ProgressReporter(Protocol):
    """A decoupled progress reporting interface for long-running tasks.

    Implementations can render progress bars, spinners, or logs. Service layer
    should depend only on this protocol, not on any concrete UI library.
    """

    def begin(self, total: int, description: str = "") -> None:
        """Initialize a progress task with a known total."""
        ...

    def advance(self, step: int = 1, detail: Optional[str] = None) -> None:
        """Advance the current progress by `step`. Optionally show `detail`."""
        ...

    def complete(self) -> None:
        """Mark the current progress task as completed."""
        ...

    def info(self, message: str) -> None:
        """Emit an informational message without affecting progress."""
        ...

    def fail(self, detail: Optional[str] = None, error: Optional[Exception] = None) -> None:
        """Emit a failure message; does not raise. Caller should still raise if needed."""
        ...


class NullProgressReporter:
    """No-op implementation used when progress should be disabled."""

    def begin(self, total: int, description: str = "") -> None:
        pass

    def advance(self, step: int = 1, detail: Optional[str] = None) -> None:
        pass

    def complete(self) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def fail(self, detail: Optional[str] = None, error: Optional[Exception] = None) -> None:
        pass


class RichProgressReporter:
    """Rich-based console progress reporter.

    Designed to be created in CLI layer and injected into services. Safe to use
    from async loops running in the same process.
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        # Use shared global console by default for better integration with Rich logging
        self.console = console or get_console()
        self._progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            expand=True,
            console=self.console,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        self._task_id: Optional[TaskID] = None
        self._running: bool = False
        self._base_desc: str = "Processing"

    def begin(self, total: int, description: str = "") -> None:
        # Lazily start progress context
        if not self._running:
            self._progress.start()
            self._running = True
        # Reset previous task if any
        if self._task_id is not None:
            try:
                self._progress.remove_task(self._task_id)
            except Exception:
                pass
        # Keep a stable base description and only show the latest detail
        self._base_desc = description or "Processing"
        self._task_id = self._progress.add_task(self._base_desc, total=total)

    def advance(self, step: int = 1, detail: Optional[str] = None) -> None:
        if self._task_id is None:
            return
        if detail:
            try:
                # Show only the latest file, avoid accumulating a long chain
                self._progress.update(self._task_id, advance=step, description=f"{self._base_desc} → {detail}")
            except Exception:
                self._progress.update(self._task_id, advance=step)
        else:
            # Restore to base description when no detail is provided
            try:
                self._progress.update(self._task_id, advance=step, description=self._base_desc)
            except Exception:
                self._progress.update(self._task_id, advance=step)

    def complete(self) -> None:
        if self._task_id is not None:
            try:
                remaining = (self._progress.tasks[0].total or 0) - (self._progress.tasks[0].completed or 0)
            except Exception:
                remaining = 0
            if remaining > 0:
                self._progress.update(self._task_id, advance=remaining)
        # Stop the progress display
        if self._running:
            try:
                self._progress.stop()
            except Exception:
                pass
            self._running = False
        self._task_id = None

    def info(self, message: str) -> None:
        try:
            self.console.log(message)
        except Exception:
            pass

    def fail(self, detail: Optional[str] = None, error: Optional[Exception] = None) -> None:
        msg = "❌ Failed"
        if detail:
            msg += f": {detail}"
        if error:
            msg += f" ({error})"
        try:
            self.console.log(msg, style="bold red")
        except Exception:
            pass