from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: OpenAI | None = None


class LLMNotConfigured(RuntimeError):
    pass


def get_client() -> OpenAI:
    """Lazily build an OpenAI-compatible client pointed at DeepSeek."""
    global _client
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise LLMNotConfigured(
            "DEEPSEEK_API_KEY is not set. Add it to .env to enable the agent."
        )
    if _client is None:
        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    temperature: float = 0.3,
) -> Any:
    """Single DeepSeek chat completion call. Returns the raw message object."""
    settings = get_settings()
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": settings.agent_max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice

    logger.debug("deepseek.chat model=%s msgs=%d tools=%s", settings.deepseek_model, len(messages), bool(tools))
    completion = client.chat.completions.create(**kwargs)
    return completion.choices[0].message
