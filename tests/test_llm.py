from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agents.llm import cached_system, cached_tools, create_message

TOOLS = [
    {"name": "search_products", "description": "a", "input_schema": {"type": "object"}},
    {"name": "get_product", "description": "b", "input_schema": {"type": "object"}},
]


def test_cached_tools_marks_only_last_and_does_not_mutate():
    out = cached_tools(TOOLS)
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in t for t in TOOLS)
    assert cached_tools([]) == []


def test_cached_system_block():
    assert cached_system("hi") == [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]


def _fake_client():
    usage = SimpleNamespace(input_tokens=1, output_tokens=1,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(usage=usage, stop_reason="end_turn")
    return client


def test_create_message_sends_cached_system_and_tools(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    client = _fake_client()
    msgs = [{"role": "user", "content": "x"}]
    create_message(client, system="sys", messages=msgs, tools=TOOLS)
    kw = client.messages.create.call_args.kwargs
    assert kw["model"] == "test-model"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"] is msgs


def test_create_message_requires_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    with pytest.raises(RuntimeError):
        create_message(_fake_client(), system="s", messages=[])
