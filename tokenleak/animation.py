"""Token leak animation — a live Rich display shown during scanning."""

import random
import threading
import time
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.text import Text

_DRAIN_CHARS = list("$¢€£₽₿0123456789")
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

    def add(self, tokens: int) -> None:
        with self._lock:
            self._total += tokens

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def _render(self) -> Text:
        drain = " ".join(random.choices(_DRAIN_CHARS, k=random.randint(6, 18)))
        t = Text()
        t.append("🔍 ", style="bold cyan")
        t.append(f"{self.repo}\n", style="cyan")
        t.append("🤖 ", style="dim")
        t.append(f"{self.model}\n", style="dim")
        t.append("━" * 50 + "\n", style="dim red")
        t.append("💸 Tokens leaked: ", style="yellow")
        t.append(f"{self._total:,}", style="bold red")
        t.append("\n", style="")
        t.append(f"  {drain}", style="bold red")
        t.append("  <<<< LEAKING >>>>", style="blink bold red")
        return t

    def _animate(self) -> None:
        with Live(self._render(), console=_console, refresh_per_second=4) as live:
            self._live = live
            while self._running:
                time.sleep(0.25)
                with self._lock:
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
