"""The buyer confirms the constraints; the agent can't change them; the receipt records exactly them."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import ledger, mandates, scenarios, shop
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
    monkeypatch.setattr(shop.checkout, "start", lambda paypal, rid, **kw: {"status": "COMPLETED"})
    result = shop.buy(REQUEST, paypal=None, client=agent_client(sneaky), constraints=CONFIRMED,
                      confirmed_at="2026-11-11T10:00:00+00:00")
    receipt = ledger.get_receipt(result["receipt_id"])
    assert receipt["mandate"]["constraints"] == CONFIRMED
    assert receipt["mandate"]["request_text"] == REQUEST
    assert receipt["mandate"]["confirmed_by_buyer_at"] == "2026-11-11T10:00:00+00:00"  # as given, not "now"
    assert receipt["mandate"]["confirmation"] == "buyer"
    assert ledger.verify_chain()["ok"] is True


def test_without_confirmed_constraints_nothing_changes(monkeypatch):
    monkeypatch.setattr(shop.checkout, "start", lambda paypal, rid, **kw: {"status": "COMPLETED"})
    c = agent_client({"constraints": CONFIRMED})
    result = shop.buy(REQUEST, paypal=None, client=c)
    receipt = ledger.get_receipt(result["receipt_id"])
    assert receipt["mandate"]["constraints"] == CONFIRMED and "confirmed_by_buyer_at" not in receipt["mandate"]
    assert receipt["mandate"]["confirmation"] == "cli"


def test_scenario_receipts_record_scripted_confirmation():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    mandate_ = ledger.get_receipt(rid)["mandate"]
    assert mandate_["confirmation"] == "scripted" and "confirmed_by_buyer_at" not in mandate_


# --- confirmation is a backend requirement ---

def parsed(client):
    with patch("backend.app.main.mandate.parse", return_value=CONFIRMED):
        r = client.post("/mandate/parse", json={"request_text": REQUEST})
    assert r.status_code == 200
    return r.json()


def test_parse_stores_the_mandate_unconfirmed():
    body = parsed(TestClient(app))
    assert body["mandate_id"].startswith("mnd_") and body["constraints"] == CONFIRMED
    assert body["request_text"] == REQUEST and body["confirmed_by_buyer_at"] is None


def test_unconfirmed_mandate_is_rejected_by_shop():
    client = TestClient(app)
    body = parsed(client)
    mandate_id = body["mandate_id"]
    with patch("backend.app.main.shop.buy") as buy:
        r = client.post("/shop", json={"mandate_id": mandate_id, "confirm_token": body["confirm_token"]})
    assert r.status_code == 422 and "confirmed" in r.json()["detail"]
    buy.assert_not_called()


def test_free_text_and_unknown_ids_are_rejected_by_shop():
    client = TestClient(app)
    with patch("backend.app.main.shop.buy") as buy:
        assert client.post("/shop", json={"request_text": REQUEST}).status_code == 422
        assert client.post("/shop", json={"request_text": REQUEST, "constraints": CONFIRMED}).status_code == 422
        p = parsed(client)
        both = client.post("/shop", json={"mandate_id": p["mandate_id"], "confirm_token": p["confirm_token"],
                                          "request_text": "other"})
        assert both.status_code == 422  # no side channel next to the id
        assert client.post("/shop", json={"mandate_id": "mnd_nope", "confirm_token": "t"}).status_code == 404
    buy.assert_not_called()


def test_confirmed_timestamp_comes_from_the_confirm_step_and_is_idempotent():
    client = TestClient(app)
    body = parsed(client)
    mandate_id = body["mandate_id"]
    tok = {"confirm_token": body["confirm_token"]}
    first = client.post(f"/mandate/{mandate_id}/confirm", json=tok).json()
    stamp = datetime.fromisoformat(first["confirmed_by_buyer_at"])
    assert stamp.tzinfo is not None and first["constraints"] == CONFIRMED
    assert client.post(f"/mandate/{mandate_id}/confirm", json=tok).json()["confirmed_by_buyer_at"] == first["confirmed_by_buyer_at"]
    assert client.post("/mandate/mnd_nope/confirm", json={"confirm_token": "t"}).status_code == 404

    # /shop passes that exact stamp on, not a new "now"
    with patch("backend.app.main.shop.buy", return_value={"ok": True}) as buy:
        r = client.post("/shop", json={"mandate_id": mandate_id, **tok})
    assert r.status_code == 200
    assert buy.call_args.kwargs["confirmed_at"] == first["confirmed_by_buyer_at"]
    assert buy.call_args.kwargs["constraints"] == CONFIRMED
    assert buy.call_args.args[0] == REQUEST  # the request text comes from the stored mandate


def test_receipt_carries_the_confirm_step_time_end_to_end(monkeypatch):
    monkeypatch.setattr(shop.checkout, "start", lambda paypal, rid, **kw: {"status": "COMPLETED"})
    client = TestClient(app)
    body = parsed(client)
    mandate_id = body["mandate_id"]
    tok = {"confirm_token": body["confirm_token"]}
    stamp = client.post(f"/mandate/{mandate_id}/confirm", json=tok).json()["confirmed_by_buyer_at"]
    real_buy = shop.buy
    with patch("backend.app.main.shop.buy",
               side_effect=lambda *a, **kw: real_buy(*a, client=agent_client(), **kw)):
        r = client.post("/shop", json={"mandate_id": mandate_id, **tok})
    assert r.status_code == 200
    mandate_ = ledger.get_receipt(r.json()["receipt_id"])["mandate"]
    assert mandate_["confirmed_by_buyer_at"] == stamp and mandate_["confirmation"] == "buyer"
    assert mandate_["constraints"] == CONFIRMED


def test_a_mandate_buys_once():
    client = TestClient(app)
    body = parsed(client)
    mandate_id = body["mandate_id"]
    tok = {"confirm_token": body["confirm_token"]}
    client.post(f"/mandate/{mandate_id}/confirm", json=tok)
    with patch("backend.app.main.shop.buy", return_value={"ok": True}) as buy:
        assert client.post("/shop", json={"mandate_id": mandate_id, **tok}).status_code == 200
        again = client.post("/shop", json={"mandate_id": mandate_id, **tok})
    assert again.status_code == 409 and "already been used" in again.json()["detail"]
    assert buy.call_count == 1
    # confirming a used mandate doesn't reopen it
    client.post(f"/mandate/{mandate_id}/confirm", json=tok)
    with patch("backend.app.main.shop.buy") as buy:
        assert client.post("/shop", json={"mandate_id": mandate_id, **tok}).status_code == 409
    buy.assert_not_called()


def test_staged_receipt_hash_is_unchanged():
    ledger.load_staged()
    receipt = ledger.get_receipt("rcpt_627e333bfb7045c1")
    assert receipt["hash"] == STAGED_HASH
    assert "confirmed_by_buyer_at" not in receipt["mandate"]
    assert ledger.verify_chain()["ok"] is True


# --- the confirm token: a per-mandate capability (not an identity) ---

def test_parse_returns_a_token_and_stores_only_its_hash():
    import hashlib
    from backend.ledger import store
    body = parsed(TestClient(app))
    token = body["confirm_token"]
    assert len(token) >= 32
    row = store.read("SELECT * FROM mandates WHERE mandate_id = ?", (body["mandate_id"],))[0]
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in [str(v) for v in tuple(row)]
    assert "confirm_token" not in client_confirm(TestClient(app), body)  # later responses never repeat it


def client_confirm(client, body):
    return client.post(f"/mandate/{body['mandate_id']}/confirm", json={"confirm_token": body["confirm_token"]}).json()


def test_confirm_without_or_with_the_wrong_token_is_403_and_changes_nothing():
    client = TestClient(app)
    body = parsed(client)
    url = f"/mandate/{body['mandate_id']}/confirm"
    assert client.post(url).status_code == 403
    assert client.post(url, json={}).status_code == 403
    assert client.post(url, json={"confirm_token": "wrong"}).status_code == 403
    assert mandates._get(body["mandate_id"])["confirmed_by_buyer_at"] is None
    assert client.post(url, json={"confirm_token": body["confirm_token"]}).status_code == 200


def test_shop_without_or_with_the_wrong_token_is_403_and_does_not_use_the_mandate():
    client = TestClient(app)
    body = parsed(client)
    client_confirm(client, body)
    with patch("backend.app.main.shop.buy", return_value={"ok": True}) as buy:
        assert client.post("/shop", json={"mandate_id": body["mandate_id"]}).status_code == 403
        assert client.post("/shop", json={"mandate_id": body["mandate_id"], "confirm_token": "wrong"}).status_code == 403
        buy.assert_not_called()
        # the other mandate's token doesn't work either
        other = parsed(client)
        assert client.post("/shop", json={"mandate_id": body["mandate_id"],
                                          "confirm_token": other["confirm_token"]}).status_code == 403
        assert client.post("/shop", json={"mandate_id": body["mandate_id"],
                                          "confirm_token": body["confirm_token"]}).status_code == 200


def test_a_mandate_from_before_tokens_cannot_be_confirmed():
    from backend.ledger import store
    m = mandates.create("x", {"currency": "USD"})
    with store.write() as conn:
        conn.execute("UPDATE mandates SET token_hash = NULL WHERE mandate_id = ?", (m["mandate_id"],))
    with pytest.raises(mandates.MandateForbidden):
        mandates.confirm(m["mandate_id"], m["confirm_token"])


# --- a refusal is retried once ---

def refusal_reply():
    return SimpleNamespace(content=[], stop_reason="refusal", usage=usage(), stop_details="x")


def test_shopping_retries_a_refusal_once_then_succeeds():
    submit = {"options_considered": [{"product_id": "p_03", "price": 109.99, "size": "8", "note": "fits"}],
              "chosen_product_id": "p_03", "chosen_size": "8", "decision_summary": "Best fit."}
    c = MagicMock()
    c.messages.create.side_effect = [refusal_reply(), agent_reply(tool_use("t1", "submit_choice", submit))]
    result = shopping.run(REQUEST, client=c, constraints=CONFIRMED, confirmed_at="2026-11-12T00:00:00+00:00")
    assert result["agent_run"]["chosen_product_id"] == "p_03"
    assert result["mandate"]["constraints"] == CONFIRMED
    assert c.messages.create.call_count == 2
    first, second = (call.kwargs["messages"] for call in c.messages.create.call_args_list)
    assert first is not second and first == [second[0]]  # a fresh conversation (the first attempt added nothing), same request and constraints


def test_shopping_gives_up_after_a_second_refusal():
    from backend.agents.llm import RefusalError
    c = MagicMock()
    c.messages.create.side_effect = [refusal_reply(), refusal_reply(), refusal_reply()]
    with pytest.raises(RefusalError):
        shopping.run(REQUEST, client=c, constraints=CONFIRMED, confirmed_at="2026-11-12T00:00:00+00:00")
    assert c.messages.create.call_count == 2  # exactly one retry
