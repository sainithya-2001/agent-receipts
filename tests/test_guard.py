"""Pre-payment guard: a choice that breaks the confirmed limits never reaches PayPal."""
from unittest.mock import patch

import pytest

from backend import checkout, ledger, shop

MANDATE = {"request_text": "shoes", "constraints": {"category": "running shoes", "size": "8", "max_price": 120,
                                                     "currency": "USD"}}


def run_of(pid, size="8"):
    return {"model": "m", "options_considered": [], "chosen_product_id": pid, "chosen_size": size,
            "reasoning": "r", "steps": []}


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    monkeypatch.delenv("PAYPAL_VAULT_ID", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "http://test")


ORDER = {"id": "ORD1", "status": "PAYER_ACTION_REQUIRED", "links": [{"rel": "payer-action", "href": "x"}]}


def test_blocks_over_budget_without_calling_paypal():
    rid = ledger.log_intent(MANDATE, run_of("p_04"))  # p_04 is over the $120 budget
    with patch("backend.checkout.orders.create_order") as create, patch("backend.checkout.orders.create_vaulted_order") as vaulted:
        out = checkout.start(None, rid, guard=True)
    create.assert_not_called(); vaulted.assert_not_called()
    assert out["blocked"] is True and out["status"] == "BLOCKED"
    assert [v["field"] for v in out["violations"]] == ["price"]
    event = ledger.get_receipt(rid)["events"][-1]
    assert event["kind"] == "purchase_blocked" and event["product_id"] == "p_04" and event["guard"] == "on"
    assert event["violations"][0]["attempted"] != "" and event["price"]
    assert ledger.verify_chain()["ok"]


def test_blocks_wrong_size_without_calling_paypal():
    rid = ledger.log_intent(MANDATE, run_of("p_03", size="9"))
    with patch("backend.checkout.orders.create_order") as create:
        out = checkout.start(None, rid, guard=True)
    create.assert_not_called()
    assert out["blocked"] and [v["field"] for v in out["violations"]] == ["size"]
    assert out["violations"][0] == {"field": "size", "asked": "8", "attempted": "9"}


def test_blocks_wrong_currency_and_category():
    rid = ledger.log_intent({"request_text": "x", "constraints": {"currency": "EUR", "category": "sandals"}}, run_of("p_03"))
    with patch("backend.checkout.orders.create_order") as create:
        out = checkout.start(None, rid, guard=True)
    create.assert_not_called()
    assert {v["field"] for v in out["violations"]} == {"currency", "category"}


def test_allows_in_mandate_purchase():
    rid = ledger.log_intent(MANDATE, run_of("p_03"))
    with patch("backend.checkout.orders.create_order", return_value=ORDER) as create:
        out = checkout.start(None, rid, guard=True)
    create.assert_called_once()
    assert "blocked" not in out and out["order_id"] == "ORD1"
    created = ledger.get_receipt(rid)["events"][0]
    assert created["kind"] == "paypal_order_created" and created["guard"] == "on"


def test_guard_off_records_it_and_still_orders():
    rid = ledger.log_intent(MANDATE, run_of("p_04"))  # violates, but this platform has no guard
    with patch("backend.checkout.orders.create_order", return_value=ORDER) as create:
        out = checkout.start(None, rid)
    create.assert_called_once()
    assert "blocked" not in out
    assert ledger.get_receipt(rid)["events"][0]["guard"] == "off"


def test_shop_buy_with_guard_blocks_and_reports(monkeypatch):
    decision = {"mandate": {**MANDATE}, "agent_run": run_of("p_04")}
    monkeypatch.setattr("backend.shop.shopping.run", lambda *a, **k: decision)
    seen = []
    with patch("backend.checkout.orders.create_order") as create:
        out = shop.buy("shoes", None, progress=seen.append, guard=True)
    create.assert_not_called()
    assert out["checkout"]["blocked"] and seen[-1]["kind"] == "blocked"
    assert out["receipt_id"]  # the intent was still logged first


def test_public_shop_route_turns_the_guard_on(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import mandates
    from backend.app.main import app
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id"); monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s")
    m = mandates.create("shoes", {"size": "8", "max_price": 120.0, "currency": "USD"})
    mandates.confirm(m["mandate_id"])
    seen = {}

    def fake_buy(*a, **kw):
        seen.update(kw)
        return {"receipt_id": "r"}
    with patch("backend.app.main.shop.buy", side_effect=fake_buy):
        assert TestClient(app).post("/shop", json={"mandate_id": m["mandate_id"]}).status_code == 200
    assert seen["guard"] is True


# --- mandates: persisted, and retryable until an order may exist ---

def _confirmed():
    from backend import mandates
    m = mandates.create("shoes", {"size": "8", "max_price": 120.0, "currency": "USD"})
    return mandates.confirm(m["mandate_id"])["mandate_id"]


def _post(monkeypatch, buy):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id"); monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    mid = _confirmed()
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", side_effect=buy):
        return client, mid, client.post("/shop", json={"mandate_id": mid})


def test_mandates_live_in_the_database_not_in_process_memory():
    from backend import mandates
    from backend.ledger import store
    m = mandates.create("shoes", {"size": "8"})
    assert store.read("SELECT request_text FROM mandates WHERE mandate_id = ?", (m["mandate_id"],))[0][0] == "shoes"
    ledger.clear()  # a fresh database: gone, with a clear 404 reason
    with pytest.raises(mandates.MandateNotFound):
        mandates.confirm(m["mandate_id"])
    assert ledger.verify_chain()["ok"]  # never part of the hash chain


def test_failed_before_payment_can_retry_with_the_same_mandate(monkeypatch):
    from backend.agents.shopping import ShoppingError
    calls = []

    def buy(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ShoppingError("agent hit max_tokens")
        return {"receipt_id": "r"}
    client, mid, first = _post(monkeypatch, buy)
    assert first.status_code == 422
    with patch("backend.app.main.shop.buy", side_effect=buy):
        assert client.post("/shop", json={"mandate_id": mid}).status_code == 200
        assert client.post("/shop", json={"mandate_id": mid}).status_code == 409  # a success uses it up


def test_checkout_precheck_failure_is_retryable(monkeypatch):
    def buy(*a, progress=None, **kw):
        progress({"kind": "ordering"})
        raise checkout.CheckoutError("Ledger failed verification")  # raised before any PayPal call
    client, mid, first = _post(monkeypatch, buy)
    assert first.status_code == 422
    with patch("backend.app.main.shop.buy", return_value={"receipt_id": "r"}):
        assert client.post("/shop", json={"mandate_id": mid}).status_code == 200


def test_failure_once_ordering_began_keeps_the_mandate_used(monkeypatch):
    from backend.paypal.client import PayPalError

    def buy(*a, progress=None, **kw):
        progress({"kind": "ordering"})
        raise PayPalError(500, "boom", None)  # the order may or may not exist
    client, mid, first = _post(monkeypatch, buy)
    assert first.status_code == 502
    with patch("backend.app.main.shop.buy", return_value={"receipt_id": "r"}) as again:
        assert client.post("/shop", json={"mandate_id": mid}).status_code == 409
    again.assert_not_called()


def test_blocked_purchase_is_not_a_failure_and_keeps_the_mandate_used(monkeypatch):
    client, mid, first = _post(monkeypatch, lambda *a, **kw: {"receipt_id": "r", "checkout": {"blocked": True}})
    assert first.status_code == 200
    assert client.post("/shop", json={"mandate_id": mid}).status_code == 409
