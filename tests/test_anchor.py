import json
from unittest.mock import patch

import pytest

from backend import checkout, ledger
from backend.ledger import chain, store

MANDATE = {"request_text": "Buy me running shoes, size 8, under $120",
           "constraints": {"category": "running shoes", "size": "8", "max_price": 120, "currency": "USD"}}
AGENT_RUN = {"model": "m", "options_considered": [{"product_id": "p_03", "price": 109.99, "attrs": {"size": "8"}}],
             "chosen_product_id": "p_03", "chosen_size": "8", "reasoning": "fits", "steps": []}


@pytest.fixture(autouse=True)
def vaulted(monkeypatch):
    monkeypatch.setenv("PAYPAL_VAULT_ID", "VAULT1")


def buy():
    """Log an intent and check out through the real checkout code, with PayPal mocked.
    Returns the receipt_id and a fake GET-order that echoes what checkout sent."""
    rid = ledger.log_intent(MANDATE, AGENT_RUN)
    order = {"id": "ORD1", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP1"}]}}]}
    with patch("backend.checkout.orders.create_vaulted_order", return_value=order) as create:
        checkout.start(None, rid)
    sent = create.call_args.kwargs
    paypal_order = {"id": "ORD1", "purchase_units": [{"custom_id": sent["custom_id"], "invoice_id": sent["invoice_id"]}]}
    return rid, paypal_order


def verify(rid, paypal_order, **kwargs):
    with patch("backend.ledger.anchor.orders.get_order", return_value=paypal_order) as get:
        result = ledger.verify_receipt_against_paypal(rid, None, **kwargs)
    return result, get


def rewrite_chain(edit):
    """The attack: control the database, edit the intent, then recompute every hash after it."""
    conn = store.connection()
    conn.executescript("DROP TRIGGER records_no_update; DROP TRIGGER records_no_delete;")
    prev = ledger.GENESIS_HASH
    for row in conn.execute("SELECT seq, body FROM records ORDER BY seq").fetchall():
        body = json.loads(row["body"])
        if "mandate" in body:
            edit(body)
        body["prev_hash"] = prev
        prev = chain.record_hash(body)
        conn.execute("UPDATE records SET body = ?, hash = ? WHERE seq = ?", (chain.canonical_json(body), prev, row["seq"]))


def test_checkout_anchors_intent_hash_as_invoice_id():
    rid, paypal_order = buy()
    receipt = ledger.get_receipt(rid)
    assert paypal_order["purchase_units"][0]["invoice_id"] == receipt["hash"]
    assert receipt["events"][0]["invoice_id"] == receipt["hash"]


def test_redirect_checkout_anchors_too(monkeypatch):
    monkeypatch.delenv("PAYPAL_VAULT_ID")
    rid = ledger.log_intent(MANDATE, AGENT_RUN)
    with patch("backend.checkout.orders.create_order", return_value={"id": "ORD1", "status": "PAYER_ACTION_REQUIRED"}) as create:
        checkout.start(None, rid)
    assert create.call_args.kwargs["invoice_id"] == ledger.get_receipt(rid)["hash"]


def test_untouched_receipt_matches_paypal():
    rid, paypal_order = buy()
    result, get = verify(rid, paypal_order)
    assert result["ok"] is True and result["anchored"] is True and result["error"] is None
    assert get.call_args.args[1] == "ORD1"


def test_full_rewrite_passes_verify_chain_but_fails_paypal_anchor():
    rid, paypal_order = buy()
    ledger.log_intent(MANDATE, AGENT_RUN)  # later records get rehashed too
    original = ledger.get_receipt(rid)["hash"]

    def buyer_said_size_9(body):
        if body["receipt_id"] == rid:
            body["mandate"]["constraints"]["size"] = "9"
            body["mandate"]["request_text"] = "Buy me running shoes, size 9, under $120"
    rewrite_chain(buyer_said_size_9)

    assert ledger.get_receipt(rid)["mandate"]["constraints"]["size"] == "9"
    assert ledger.get_receipt(rid)["hash"] != original
    assert ledger.verify_chain()["ok"] is True  # the chain alone can't tell
    result, _ = verify(rid, paypal_order)
    assert result["ok"] is False and result["anchored"] is True
    assert result["invoice_id"] == original != result["intent_hash"]
    assert "changed after the order was created" in result["error"]


def test_stored_hash_is_not_trusted():
    """Editing the body but leaving the stored hash alone must still fail: the hash is recomputed."""
    rid, paypal_order = buy()
    conn = store.connection()
    conn.executescript("DROP TRIGGER records_no_update;")
    body = json.loads(conn.execute("SELECT body FROM records WHERE seq = 1").fetchone()["body"])
    body["mandate"]["constraints"]["max_price"] = 500.0
    conn.execute("UPDATE records SET body = ? WHERE seq = 1", (chain.canonical_json(body),))
    assert verify(rid, paypal_order)[0]["ok"] is False


def test_order_for_another_receipt_fails():
    rid, paypal_order = buy()
    paypal_order["purchase_units"][0]["custom_id"] = "rcpt_ffffffffffffffff"
    result, _ = verify(rid, paypal_order)
    assert result["ok"] is False and "belongs to" in result["error"]


def test_order_id_from_dispute_overrides_ledger_events():
    rid, paypal_order = buy()
    _, get = verify(rid, paypal_order, order_id="ORD_FROM_DISPUTE")
    assert get.call_args.args[1] == "ORD_FROM_DISPUTE"


def test_unanchored_order_is_reported_not_passed():
    ledger.load_staged()
    staged_order = {"id": "3DS712773U002684V", "purchase_units": [{"custom_id": "rcpt_627e333bfb7045c1"}]}
    result, _ = verify("rcpt_627e333bfb7045c1", staged_order)
    assert result["ok"] is False and result["anchored"] is False and "predates anchoring" in result["error"]


def test_missing_receipt_or_order():
    assert ledger.verify_receipt_against_paypal("rcpt_0000000000000000", None)["error"] == "receipt not in the ledger"
    rid = ledger.log_intent(MANDATE, AGENT_RUN)
    assert ledger.verify_receipt_against_paypal(rid, None)["error"] == "receipt has no PayPal order"


def test_capture_id_from_dispute_is_checked_via_the_capture():
    rid, paypal_order = buy()
    capture = {**paypal_order["purchase_units"][0], "id": "CAP1",
               "links": [{"rel": "up", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORD1"}]}
    with (
        patch("backend.ledger.anchor.payments.get_capture", return_value=capture) as get_capture,
        patch("backend.ledger.anchor.orders.get_order") as get_order,
    ):
        result = ledger.verify_receipt_against_paypal(rid, None, capture_id="CAP1")
    get_order.assert_not_called()
    assert get_capture.call_args.args[1] == "CAP1"
    assert result["ok"] is True and result["order_id"] == "ORD1" and result["capture_id"] == "CAP1"

    def size_9(body):
        body["mandate"]["constraints"]["size"] = "9"
    rewrite_chain(size_9)
    with patch("backend.ledger.anchor.payments.get_capture", return_value=capture):
        assert ledger.verify_receipt_against_paypal(rid, None, capture_id="CAP1")["ok"] is False
