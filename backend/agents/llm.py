"""Shared Claude call for all agents, with prompt caching on the system prompt and tools.

Cache prefix order is tools -> system -> messages. We put one breakpoint on the last
tool and one on the system prompt, so the stable prefix is reused across every turn of a
tool-use loop and across runs. Keep per-request data (mandate, dispute details, RAG
snippets) in `messages`, never in the system prompt, or the cache misses every time.
"""
import logging
from typing import Any

import anthropic

from backend.config import get_settings

log = logging.getLogger(__name__)

EPHEMERAL = {"type": "ephemeral"}


def cached_system(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "cache_control": EPHEMERAL}]


def cached_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy of `tools` with a cache breakpoint on the last one. Input is not mutated."""
    out = [dict(t) for t in tools]
    if out:
        out[-1]["cache_control"] = EPHEMERAL
    return out


def create_message(
    client: anthropic.Anthropic,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 16000,
    **kwargs: Any,
):
    model = get_settings().anthropic_model
    if not model:
        raise RuntimeError("ANTHROPIC_MODEL is not set")
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": cached_system(system),
        "messages": messages,
        **kwargs,
    }
    if tools:
        params["tools"] = cached_tools(tools)
    response = client.messages.create(**params)
    u = response.usage
    log.info(
        "claude %s stop=%s in=%s cache_write=%s cache_read=%s out=%s",
        getattr(response, "_request_id", None), response.stop_reason, u.input_tokens,
        getattr(u, "cache_creation_input_tokens", None),
        getattr(u, "cache_read_input_tokens", None), u.output_tokens,
    )
    return response
