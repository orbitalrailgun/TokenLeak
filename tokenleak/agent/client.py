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
    response = client.chat.completions.create(**kwargs)
    log.debug("AI response: usage=%s", response.usage)
    return response


def extract_usage(response: ChatCompletion) -> int:
    """Return total tokens used in the response."""
    if response.usage:
        return response.usage.total_tokens
    return 0
