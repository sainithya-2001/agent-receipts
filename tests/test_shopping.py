from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agents import shopping

CONSTRAINTS = {"category": "running shoes", "size": "8", "max_price": 120, "currency": "USD"}


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def reply(*blocks, stop="tool_use"):
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


def choice(pid="p_03", size="8"):
    return {
        "constraints": CONSTRAINTS,
        "options_considered": [{"product_id": "p_03", "price": 109.99, "size": "8", "note": "fits"}],
        "chosen_product_id": pid,
        "chosen_size": size,
        "decision_summary": "Best cushioned option under budget.",
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    c = MagicMock()
    return c


def test_run_searches_then_submits(client):
    client.messages.create.side_effect = [
        reply(tool_use("t1", "search_products",
                       {"query": None, "category": "running shoes", "size": "8", "max_price": 120})),
        reply(tool_use("t2", "submit_choice", choice())),
    ]
    result = shopping.run("Buy me running shoes, size 8, under $120", client=client)

    assert result["mandate"]["constraints"] == CONSTRAINTS
    run = result["agent_run"]
    assert run["chosen_product_id"] == "p_03" and run["model"] == "test-model"
    assert [s["tool"] for s in run["steps"]] == ["search_products", "submit_choice"]
    assert "p_03" in run["steps"][0]["result_ids"]
    # Tool fields map onto the receipt schema's names.
    assert run["reasoning"] == "Best cushioned option under budget."
    assert run["options_considered"] == [{"product_id": "p_03", "price": 109.99, "attrs": {"size": "8"}, "reason": "fits"}]
    # Turn 3 (user) carries the tool_result for t1. The mock holds the live list, so index, not [-1].
    second_msgs = client.messages.create.call_args_list[1].kwargs["messages"]
    assert second_msgs[2]["content"][0]["tool_use_id"] == "t1"


def test_invalid_choice_is_returned_as_error_and_agent_can_retry(client):
    client.messages.create.side_effect = [
        reply(tool_use("t1", "submit_choice", choice(pid="p_03", size="12"))),
        reply(tool_use("t2", "submit_choice", choice())),
    ]
    result = shopping.run("shoes", client=client)
    assert result["agent_run"]["steps"][0]["error"].startswith("p_03 is not sold in size")
    err = client.messages.create.call_args_list[1].kwargs["messages"][2]["content"][0]
    assert err["is_error"] is True


def test_over_budget_choice_is_recorded_not_blocked(client):
    # Mandate violations are the adjudicator's job; the receipt must show what the agent did.
    client.messages.create.side_effect = [reply(tool_use("t1", "submit_choice", choice(pid="p_04")))]
    assert shopping.run("shoes", client=client)["agent_run"]["chosen_product_id"] == "p_04"


def test_stopping_without_submit_raises(client):
    client.messages.create.side_effect = [reply(SimpleNamespace(type="text", text="Hmm."), stop="end_turn")]
    with pytest.raises(shopping.ShoppingError):
        shopping.run("shoes", client=client)


def test_tools_are_strict_and_cached_last(client):
    client.messages.create.side_effect = [reply(tool_use("t1", "submit_choice", choice()))]
    shopping.run("shoes", client=client)
    tools = client.messages.create.call_args.kwargs["tools"]
    assert all(t["strict"] for t in tools)
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
