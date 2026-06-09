"""Token leak animation — a live Rich display shown during scanning."""

import threading
import time
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.text import Text

_SPIN = ["|", "/", "-", "\\"]
_ENABLED = True
_console = Console(stderr=True)


def _progress_bar(current: int, total: int, width: int = 20) -> tuple[str, str]:
    if total <= 0:
        return "░" * width, "–"
    filled = min(width, round(current * width / total))
    return "█" * filled + "░" * (width - filled), f"{current}/{total}"


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled


class TokenCounter:
    """Thread-safe token counter with live animation — one instance per repository."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._input = 0
        self._output = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._started = False
        self._frame = 0
        self._commit_sha: str = ""
        self._branch: str = ""
        self._author: str = ""
        self._date_str: str = ""
        self._mode: str = ""
        self._data_size: str = ""
        self._action: str = ""
        self._commit_cur: int = 0
        self._commit_total: int = 0
        self._file_cur: int = 0
        self._file_total: int = 0

    # ── State setters (thread-safe) ───────────────────────────────────────────

    def set_commit(
        self,
        sha: str,
        *,
        branch: str = "",
        author: str = "",
        date: Optional[datetime] = None,
        mode: str = "",
        data_size: str = "",
    ) -> None:
        with self._lock:
            self._commit_sha = sha[:12] if sha else ""
            self._branch = branch
            self._author = author
            self._date_str = date.strftime("%Y-%m-%d %H:%M") if date else ""
            self._mode = mode
            self._data_size = data_size
            self._action = ""
        # In noanimation mode (explicit --noanimation in a real terminal) print a brief commit line
        if not _ENABLED and _console.is_terminal and sha:
            sha_short = sha[:12]
            parts: list[str] = [sha_short]
            if branch:
                parts.append(f"({branch})")
            if mode:
                parts.append(f"[{mode}]")
            if data_size:
                parts.append(f"— {data_size}")
            _console.print(f"[dim]  · {' '.join(parts)}[/dim]")

    def add(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self._input += input_tokens
            self._output += output_tokens

    def set_action(self, msg: str) -> None:
        with self._lock:
            self._action = msg[:80] if msg else ""

    def set_commit_progress(self, current: int, total: int) -> None:
        with self._lock:
            self._commit_cur = current
            self._commit_total = total

    def set_file_progress(self, current: int, total: int) -> None:
        with self._lock:
            self._file_cur = current
            self._file_total = total

    @property
    def total(self) -> int:
        with self._lock:
            return self._input + self._output

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> Text:
        # Called while self._lock is held by _animate — reads are safe
        s = _SPIN[self._frame % 4]
        sha = self._commit_sha
        branch = self._branch
        author = self._author
        date_str = self._date_str
        mode = self._mode
        data_size = self._data_size
        action = self._action
        inp = self._input
        out = self._output

        t = Text()
        t.append("🤖 ", style="dim")
        t.append(self.model + "\n", style="dim")
        t.append("\n")

        if sha:
            t.append("  commit  ", style="dim")
            t.append(sha, style="bold yellow")
            if mode:
                t.append(f"  [{mode}]", style="dim cyan")
            t.append("\n")
        if branch:
            t.append("  branch  ", style="dim")
            t.append(branch + "\n", style="cyan")
        if author:
            t.append("  author  ", style="dim")
            t.append(author + "\n", style="dim")
        if date_str:
            t.append("  date    ", style="dim")
            t.append(date_str + "\n", style="dim")
        if data_size:
            t.append("  data    ", style="dim")
            t.append(data_size + "\n", style="dim")

        t.append("\n")

        commit_total = self._commit_total
        file_total = self._file_total
        if commit_total > 0:
            bar, frac = _progress_bar(self._commit_cur, commit_total)
            t.append("  commits ", style="dim")
            t.append(f"[{bar}]", style="bold green")
            t.append(f"  {frac}\n", style="dim")
        if file_total > 0:
            bar, frac = _progress_bar(self._file_cur, file_total)
            t.append("  files   ", style="dim")
            t.append(f"[{bar}]", style="bold cyan")
            t.append(f"  {frac}\n", style="dim")
        if commit_total > 0 or file_total > 0:
            t.append("\n")

        if action:
            t.append(f"  ⚙  {action}\n", style="dim cyan")
            t.append("\n")

        t.append("  💸 ", style="yellow")
        t.append(f"{inp:,}", style="bold red")
        t.append(" in  ", style="dim")
        t.append(f"{out:,}", style="bold magenta")
        t.append(" out", style="dim")
        t.append(f"   {s}", style="bold red")

        return t

    # ── Thread ────────────────────────────────────────────────────────────────

    def _animate(self) -> None:
        with Live(self._render(), console=_console, refresh_per_second=6, transient=True) as live:
            while self._running:
                time.sleep(1 / 6)
                with self._lock:
                    self._frame += 1
                    live.update(self._render())

    def start(self) -> None:
        self._started = True
        if not _ENABLED or not _console.is_terminal:
            return
        self._running = True
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        inp = self._input
        out = self._output
        if inp + out > 0:
            _console.print(
                f"[yellow]💸 Tokens:[/yellow] "
                f"[bold red]{inp:,}[/bold red][dim] in[/dim]  "
                f"[bold magenta]{out:,}[/bold magenta][dim] out[/dim]  "
                f"[dim]({inp + out:,} total)[/dim]"
            )
        else:
            _console.print("[dim]  · 0 tokens[/dim]")


def simple_print(total_tokens: int) -> None:
    """Fallback one-liner for --noanimation mode."""
    _console.print(
        f"[dim]tokens:[/dim] [red]{total_tokens:,}[/red]", end="\r"
    )
