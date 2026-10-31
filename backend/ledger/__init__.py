"""Receipts ledger: hash-chained intents and the PayPal events linked to them.

    from backend import ledger
    rid = ledger.log_intent(mandate, agent_run)   # before any PayPal call
    ledger.append_event(rid, "paypal_order_created", {"order_id": ...})
"""
from backend.ledger.anchor import verify_receipt_against_paypal
from backend.ledger.chain import (
    GENESIS_HASH,
    InvalidReceipt,
    LedgerError,
    append_event,
    events_of_kinds,
    find_by_order,
    get_receipt,
    log_intent,
    verify_chain,
)
from backend.ledger.staged import load_staged
from backend.ledger.store import clear, configure

__all__ = [
    "GENESIS_HASH", "InvalidReceipt", "LedgerError", "append_event", "clear", "configure", "events_of_kinds", "find_by_order",
    "get_receipt", "load_staged", "log_intent", "verify_chain", "verify_receipt_against_paypal",
]
