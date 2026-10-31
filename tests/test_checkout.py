from unittest.mock import patch

import pytest

from backend import checkout, ledger

MANDATE = {"request_text": "shoes", "constraints": {"size": "8", "max_price": 120}}


def agent_run(pid="p_03"):
    return {"model": "m", "options_considered": [], "chosen_product_id": pid, "chosen_size": "8",
            "reasoning": "r", "steps": []}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "http://test")
    monkeypatch.delenv("PAYPAL_VAULT_ID", raising=False)
    ledger.clear()
    yield
    ledger.clear()


def test_no_order_without_receipt():
    with patch("backend.checkout.orders.create_order") as create:
        with pytest.raises(checkout.CheckoutError):
            checkout.start(None, "")
        with pytest.raises(checkout.CheckoutError):
            checkout.start(None, "rcpt_missing")
        create.assert_not_called()


def test_start_uses_catalog_price_and_links_receipt():
    rid = ledger.log_intent(MANDATE, agent_run())
    order = {"id": "ORD1", "status": "PAYER_ACTION_REQUIRED",
             "links": [{"rel": "payer-action", "href": "https://paypal/approve"}]}
    with patch("backend.checkout.orders.create_order", return_value=order) as create:
        out = checkout.start(None, rid)

    args, kwargs = create.call_args
    assert args[1] == "109.99"
    assert kwargs["custom_id"] == rid
    assert kwargs["return_url"] == "http://test/paypal/return"
    assert out["approve_url"] == "https://paypal/approve"
    events = ledger.get_receipt(rid)["events"]
    assert events[0]["kind"] == "paypal_order_created" and events[0]["order_id"] == "ORD1"


def test_no_order_when_agent_chose_nothing():
    rid = ledger.log_intent(MANDATE, agent_run(pid=None))
    with pytest.raises(checkout.CheckoutError):
        checkout.start(None, rid)


def test_complete_appends_capture_without_touching_intent():
    rid = ledger.log_intent(MANDATE, agent_run())
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1"})
    before = ledger.get_receipt(rid)
    captured = {"status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP1"}]}}]}
    with patch("backend.checkout.orders.capture_order", return_value=captured):
        out = checkout.complete(None, "ORD1")

    assert out == {"receipt_id": rid, "order_id": "ORD1", "status": "COMPLETED", "capture_id": "CAP1"}
    after = ledger.get_receipt(rid)
    assert after["mandate"] == before["mandate"] and after["agent_run"] == before["agent_run"]
    assert [e["kind"] for e in after["events"]] == ["paypal_order_created", "paypal_captured"]


def test_vaulted_checkout_completes_without_redirect(monkeypatch):
    monkeypatch.setenv("PAYPAL_VAULT_ID", "VAULT1")
    rid = ledger.log_intent(MANDATE, agent_run())
    order = {"id": "ORD2", "status": "COMPLETED",
             "purchase_units": [{"payments": {"captures": [{"id": "CAP2"}]}}]}
    with (
        patch("backend.checkout.orders.create_vaulted_order", return_value=order) as create,
        patch("backend.checkout.orders.create_order") as redirect_create,
    ):
        out = checkout.start(None, rid)

    redirect_create.assert_not_called()
    args, kwargs = create.call_args
    assert args[1:] == ("109.99", "VAULT1") and kwargs["custom_id"] == rid
    assert out["status"] == "COMPLETED" and out["capture_id"] == "CAP2" and "approve_url" not in out
    events = ledger.get_receipt(rid)["events"]
    assert [e["kind"] for e in events] == ["paypal_order_created", "paypal_captured"]
    assert events[0]["vaulted"] is True and events[1]["capture_id"] == "CAP2"
