"""The buyer confirms the constraints; the agent can't change them; the receipt records exactly them."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import ledger, shop
from backend.agents import mandate, shopping
from backend.app.main import app

CONFIRMED = {"category": "running shoes", "size": "8", "max_price": 120.0, "currency": "USD"}
REQUEST = "Buy me running shoes, size 8, under $120"
STAGED_HASH = "d0beb673944e6288205f4bd4d06ba408e48549f20e3bcb0665c0b74e699f5ad9"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")


def usage():
    return SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def structured(answer):
    c = MagicMock()
    c.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(answer))], stop_reason="end_turn", usage=usage())
    return c


# --- parsing ---

def test_parse_returns_constraints_from_structured_output():
    client = structured(CONFIRMED)
    assert mandate.parse(REQUEST, client=client) == CONFIRMED
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "reasoning" not in json.dumps(mandate.SCHEMA)  # that field name trips the model's refusal
    assert "running shoes" in kwargs["messages"][0]["content"]  # known categories are offered


def test_parse_rejects_empty_and_unreadable():
    with pytest.raises(mandate.MandateError):
        mandate.parse("   ", client=structured(CONFIRMED))
    bad = MagicMock()
    bad.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="not json")], stop_reason="end_turn", usage=usage())
    with pytest.raises(mandate.MandateError):
        mandate.parse(REQUEST, client=bad)


def test_parse_route_and_its_own_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    client = TestClient(app)
    with patch("backend.app.main.mandate.parse", return_value=CONFIRMED):
        first = client.post("/mandate/parse", json={"request_text": REQUEST})
        assert first.status_code == 200 and first.json()["constraints"] == CONFIRMED
        # 4x the shop limit, counted separately from /shop runs
        codes = [client.post("/mandate/parse", json={"request_text": REQUEST}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_parse_route_reports_unreadable_requests():
    with patch("backend.app.main.mandate.parse", side_effect=mandate.MandateError("no")):
        assert TestClient(app).post("/mandate/parse", json={"request_text": "x"}).status_code == 422


# --- the agent can't change confirmed constraints ---

def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def agent_reply(*blocks):
    return SimpleNamespace(content=list(blocks), stop_reason="tool_use", usage=usage())


def agent_client(extra=None):
    submit = {"options_considered": [{"product_id": "p_03", "price": 109.99, "size": "8", "note": "fits"}],
              "chosen_product_id": "p_03", "chosen_size": "8", "decision_summary": "Best fit.", **(extra or {})}
    c = MagicMock()
    c.messages.create.side_effect = [agent_reply(tool_use("t1", "submit_choice", submit))]
    return c


def test_agent_is_told_the_constraints_and_cannot_report_its_own():
    client = agent_client()
    shopping.run(REQUEST, client=client, constraints=CONFIRMED, confirmed_at="2026-11-11T00:00:00+00:00")
    kwargs = client.messages.create.call_args.kwargs
    submit = next(t for t in kwargs["tools"] if t["name"] == "submit_choice")["input_schema"]
    assert "constraints" not in submit["properties"] and "constraints" not in submit["required"]
    assert json.dumps(CONFIRMED) in kwargs["messages"][0]["content"]
    assert any("constraints" in t["input_schema"]["properties"] for t in shopping.TOOLS)  # default tools untouched


def test_receipt_constraints_equal_the_confirmed_constraints(monkeypatch):
    # Even if the agent returned different constraints, the receipt records the confirmed ones.
    sneaky = {"constraints": {"category": "running shoes", "size": "9", "max_price": 500, "currency": "USD"}}
    monkeypatch.setattr(shop.checkout, "start", lambda paypal, rid: {"status": "COMPLETED"})
    result = shop.buy(REQUEST, paypal=None, client=agent_client(sneaky), constraints=CONFIRMED)
    receipt = ledger.get_receipt(result["receipt_id"])
    assert receipt["mandate"]["constraints"] == CONFIRMED
    assert receipt["mandate"]["request_text"] == REQUEST
    stamp = datetime.fromisoformat(receipt["mandate"]["confirmed_by_buyer_at"])
    assert stamp.tzinfo is not None
    assert ledger.verify_chain()["ok"] is True


def test_without_confirmed_constraints_nothing_changes(monkeypatch):
    monkeypatch.setattr(shop.checkout, "start", lambda paypal, rid: {"status": "COMPLETED"})
    c = agent_client({"constraints": CONFIRMED})
    result = shop.buy(REQUEST, paypal=None, client=c)
    receipt = ledger.get_receipt(result["receipt_id"])
    assert receipt["mandate"]["constraints"] == CONFIRMED and "confirmed_by_buyer_at" not in receipt["mandate"]


def test_shop_route_passes_confirmed_constraints_to_the_agent():
    with patch("backend.app.main.shop.buy", return_value={"ok": True}) as buy:
        r = TestClient(app).post("/shop", json={"request_text": REQUEST, "constraints": CONFIRMED})
    assert r.status_code == 200 and buy.call_args.kwargs["constraints"] == CONFIRMED


def test_staged_receipt_hash_is_unchanged():
    ledger.load_staged()
    receipt = ledger.get_receipt("rcpt_627e333bfb7045c1")
    assert receipt["hash"] == STAGED_HASH
    assert "confirmed_by_buyer_at" not in receipt["mandate"]
    assert ledger.verify_chain()["ok"] is True
