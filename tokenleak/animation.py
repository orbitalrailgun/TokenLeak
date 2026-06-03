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


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled


class TokenCounter:
    """Thread-safe token counter with live animation — one instance per repository."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._total = 0
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
        # In noanimation mode print a brief commit line for progress feedback
        if not _ENABLED and sha:
            sha_short = sha[:12]
            parts: list[str] = [sha_short]
            if branch:
                parts.append(f"({branch})")
            if mode:
                parts.append(f"[{mode}]")
            if data_size:
                parts.append(f"— {data_size}")
            _console.print(f"[dim]  · {' '.join(parts)}[/dim]")

    def add(self, tokens: int) -> None:
        with self._lock:
            self._total += tokens

    def set_action(self, msg: str) -> None:
        with self._lock:
            self._action = msg[:80] if msg else ""

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

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
        total = self._total

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
        if action:
            t.append(f"  ⚙  {action}\n", style="dim cyan")
            t.append("\n")

        t.append("  💸 ", style="yellow")
        t.append(f"{total:,} tokens", style="bold red")
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
        if not _ENABLED:
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
        total = self._total
        if total > 0:
            _console.print(
                f"[yellow]💸 Total tokens:[/yellow] [bold red]{total:,}[/bold red]"
            )
        else:
            _console.print("[dim]  · 0 tokens[/dim]")


def simple_print(total_tokens: int) -> None:
    """Fallback one-liner for --noanimation mode."""
    _console.print(
        f"[dim]tokens:[/dim] [red]{total_tokens:,}[/red]", end="\r"
    )
