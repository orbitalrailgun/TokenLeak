"""Token leak animation — a live Rich display shown during scanning."""

import threading
import time
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.text import Text

_SPIN = ["|", "/", "-", "\\"]
_DRIP_WIDTH = 13          # number of spinner chars in the drip row
_ENABLED = True
_console = Console(stderr=True)


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled


class TokenCounter:
    """Thread-safe token counter with live animation."""

    def __init__(self, repo: str, model: str) -> None:
        self.repo = repo
        self.model = model
        self._total = 0
        self._lock = threading.Lock()
        self._live: Optional[Live] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._frame = 0
        self._commit_sha: str = ""
        self._action: str = ""

    # ── State setters (thread-safe) ───────────────────────────────────────────

    def add(self, tokens: int) -> None:
        with self._lock:
            self._total += tokens

    def set_commit(self, sha: str) -> None:
        with self._lock:
            self._commit_sha = sha[:12] if sha else ""

    def set_action(self, msg: str) -> None:
        with self._lock:
            self._action = msg[:80] if msg else ""

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> Text:
        s = _SPIN[self._frame % 4]
        drip = ("  " + s) * _DRIP_WIDTH

        t = Text()
        t.append("🔍 ", style="bold cyan")
        # truncate long repo URLs to fit
        repo_display = self.repo if len(self.repo) <= 60 else "…" + self.repo[-57:]
        t.append(f"{repo_display}\n", style="cyan")
        t.append("🤖 ", style="dim")
        t.append(f"{self.model}\n", style="dim")
        t.append("━" * 52 + "\n", style="dim red")

        t.append("💸 Tokens leaked: ", style="yellow")
        t.append(f"{self._total:,}\n", style="bold red")

        if self._commit_sha:
            t.append("   Commit: ", style="dim")
            t.append(f"{self._commit_sha}\n", style="bold yellow")

        t.append(f"\n  {s}  ", style="bold red")
        t.append("<<<< LEAKING >>>>", style="blink bold red")
        t.append(f"  {s}\n", style="bold red")
        t.append(f"{drip}\n", style="red")

        if self._action:
            t.append(f"\n  {self._action}", style="dim cyan")

        return t

    # ── Thread ────────────────────────────────────────────────────────────────

    def _animate(self) -> None:
        with Live(self._render(), console=_console, refresh_per_second=6) as live:
            self._live = live
            while self._running:
                time.sleep(1 / 6)
                with self._lock:
                    self._frame += 1
                    live.update(self._render())

    def start(self) -> None:
        if not _ENABLED:
            return
        self._running = True
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if not _ENABLED:
            _console.print(
                f"[yellow]Tokens used:[/yellow] [bold red]{self._total:,}[/bold red]"
            )


def simple_print(total_tokens: int) -> None:
    """Fallback one-liner for --noanimation mode."""
    _console.print(
        f"[dim]tokens:[/dim] [red]{total_tokens:,}[/red]", end="\r"
    )
