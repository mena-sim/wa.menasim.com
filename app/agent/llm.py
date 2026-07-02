from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.core.logging import get_logger

logger = get_logger(__name__)

# Cache clients keyed by (api_key, base_url) so we don't rebuild per request.
_clients: dict[tuple[str, str], OpenAI] = {}


class LLMNotConfigured(RuntimeError):
    pass


def get_client(api_key: str, base_url: str) -> OpenAI:
    if not api_key:
        raise LLMNotConfigured("DeepSeek API key is not set.")
    cache_key = (api_key, base_url)
    client = _clients.get(cache_key)
    if client is None:
        client = OpenAI(api_key=api_key, base_url=base_url)
        _clients[cache_key] = client
    return client


def chat(
    messages: list[dict[str, Any]],
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
) -> Any:
    """Single DeepSeek chat completion. Returns the raw message object."""
    client = get_client(api_key, base_url)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    logger.debug("deepseek.chat model=%s msgs=%d tools=%s", model, len(messages), bool(tools))
    completion = client.chat.completions.create(**kwargs)
    return completion.choices[0].message
