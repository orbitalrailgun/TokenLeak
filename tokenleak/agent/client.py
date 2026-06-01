"""Unified AI client for OpenAI and Ollama (OpenAI-compatible API).

Both providers use the `openai` Python library; Ollama is accessed by pointing
base_url at the local Ollama server's /v1 endpoint.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from openai import OpenAI
from openai.types.chat import ChatCompletion

from tokenleak.config import Config
from tokenleak.logging_setup import get_logger

log = get_logger()


class InsufficientFundsError(Exception):
    """Raised when the API rejects a request due to billing or quota exhaustion.

    This is a fatal error — there is no point retrying or continuing to scan
    other commits. The caller should stop all scanning and surface this to the user.
    """


class ContextWindowExceededError(Exception):
    """Raised when the conversation history has grown beyond the model's context window.

    The agent loop catches this and stops cleanly — the scan is not failed,
    alerts saved so far are preserved, and scanning continues with the next commit.
    """


_BILLING_PHRASES = (
    "insufficient_funds",
    "insufficient funds",
    "quota exceeded",
    "quota_exceeded",
    "out of quota",
    "billing",
    "payment required",
    "project not found",   # Zhipu AI / SambaNova message for exhausted balance
    "contact support",
    "account suspended",
    "no credits",
    "out of credits",
    "prepaid balance",
)


def is_billing_error(exc: Exception) -> bool:
    """Return True if the exception looks like an API billing / quota exhaustion error."""
    msg = str(exc).lower()
    if any(phrase in msg for phrase in _BILLING_PHRASES):
        return True
    try:
        from openai import APIStatusError
        if isinstance(exc, APIStatusError) and exc.status_code == 402:
            return True
    except ImportError:
        pass
    return False


def build_client(config: Config) -> OpenAI:
    if config.ai_provider == "ollama":
        base_url = config.ai_api_url or "http://localhost:11434/v1"
        return OpenAI(base_url=base_url, api_key="ollama")
    # OpenAI (or any OpenAI-compatible provider)
    kwargs: dict[str, Any] = {"api_key": config.ai_api_key}
    if config.ai_api_url:
        kwargs["base_url"] = config.ai_api_url
    return OpenAI(**kwargs)


def chat(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
) -> ChatCompletion:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    log.debug("AI request: model=%s messages=%d tools=%d",
              model, len(messages), len(tools or []))
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        if is_billing_error(exc):
            raise InsufficientFundsError(str(exc)) from exc
        msg = str(exc)
        if "max_tokens must be at least 1" in msg or "context_length_exceeded" in msg:
            raise ContextWindowExceededError(msg) from exc
        raise
    log.debug("AI response: usage=%s", response.usage)
    return response


def extract_usage(response: ChatCompletion) -> int:
    """Return total tokens used in the response."""
    if response.usage:
        return response.usage.total_tokens
    return 0
