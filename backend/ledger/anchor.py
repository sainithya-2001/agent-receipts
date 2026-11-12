"""Check a receipt against the copy of its hash that PayPal holds.

verify_chain() only proves the chain is self-consistent. Someone who controls the database
can edit an intent and recompute every later hash, and the chain still verifies. So checkout
puts the intent's hash in the PayPal order as invoice_id; PayPal keeps it, and we can't
rewrite it. A receipt whose recomputed hash doesn't match its order's invoice_id was changed
after the order was created.
"""
from typing import Any

from backend.ledger.chain import get_receipt, intent_hash
from backend.paypal import orders, payments
from backend.paypal.client import PayPalClient


def verify_receipt_against_paypal(
    receipt_id: str, paypal: PayPalClient, order_id: str | None = None, capture_id: str | None = None
) -> dict[str, Any]:
    """Returns {"ok", "anchored", "custom_id_ok", "receipt_id", "order_id", "capture_id", "intent_hash",
    "invoice_id", "custom_id", "error"}.

    Prefer capture_id from the dispute (disputed_transactions[].seller_transaction_id): it
    comes from PayPal, and the capture carries both custom_id and invoice_id. order_id works
    the same way. With neither, the order is taken from the ledger's own events, which an
    attacker who controls the database could also rewrite. anchored is False when PayPal has
    no invoice_id (orders created before anchoring)."""
    result: dict[str, Any] = {"ok": False, "anchored": False, "custom_id_ok": False, "receipt_id": receipt_id,
                              "order_id": order_id, "capture_id": capture_id, "intent_hash": None,
                              "invoice_id": None, "custom_id": None, "error": None}
    receipt = get_receipt(receipt_id)
    if receipt is None:
        return {**result, "error": "receipt not in the ledger"}
    result["intent_hash"] = intent_hash(receipt_id)  # recomputed locally, so it exists even with no order
    if capture_id is None and order_id is None:
        created = [e for e in receipt["events"] if e["kind"] == "paypal_order_created"]
        if not created:
            return {**result, "error": "receipt has no PayPal order"}
        order_id = created[0]["order_id"]

    if capture_id is not None:
        held = payments.get_capture(paypal, capture_id)
        result["order_id"] = payments.order_id_of(held)
        source = f"capture {capture_id}"
    else:
        held = orders.get_order(paypal, order_id)["purchase_units"][0]
        result["order_id"] = order_id
        source = f"order {order_id}"
    result["invoice_id"] = held.get("invoice_id")
    result["anchored"] = result["invoice_id"] is not None
    result["custom_id"] = held.get("custom_id")
    result["custom_id_ok"] = held.get("custom_id") == receipt_id
    if not result["custom_id_ok"]:
        return {**result, "error": f"{source} belongs to {held.get('custom_id')!r}, not this receipt"}
    if not result["anchored"]:
        return {**result, "error": f"{source} has no invoice_id; it predates anchoring"}
    if result["invoice_id"] != result["intent_hash"]:
        return {**result, "error": "intent was changed after the order was created: its hash no longer matches PayPal's invoice_id"}
    return {**result, "ok": True}
