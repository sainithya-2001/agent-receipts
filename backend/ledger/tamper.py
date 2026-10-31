"""Demo only: the attack the PayPal anchor exists to catch.

Someone who controls the database edits a receipt's intent after the purchase and recomputes
every hash after it, so verify_chain() still passes. Only the invoice_id PayPal holds (the
intent's original hash) shows the change.

Guarded so it can't touch a receipt that matters: the receipt must come from a seeded scenario
(agent_run.model == DEMO_MODEL), must not be a staged receipt, and its records must be the
tail of the chain, so no other receipt's hash (and so no other PayPal anchor) changes.
"""
import json
from collections.abc import Callable
from typing import Any

from backend.ledger import store
from backend.ledger.chain import GENESIS_HASH, INTENT, LedgerError, canonical_json, record_hash
from backend.ledger.staged import STAGED_DIR

DEMO_MODEL = "seeded-scenario"

_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records "
    "BEGIN SELECT RAISE(ABORT, 'ledger records are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records "
    "BEGIN SELECT RAISE(ABORT, 'ledger records are append-only'); END",
)


class TamperRefused(LedgerError):
    pass


def rewrite_intent(receipt_id: str, edit: Callable[[dict[str, Any]], None]) -> dict[str, str]:
    """Apply edit to the receipt's intent body, rehash it and every later record, and update
    the invoice_id the ledger recorded for its order (as an attacker would). Returns the old
    and new intent hashes."""
    if (STAGED_DIR / f"{receipt_id}.json").exists():
        raise TamperRefused(f"{receipt_id} is a staged receipt; tampering is for scenario receipts only")
    with store.write() as conn:
        intent = conn.execute("SELECT seq, body, hash FROM records WHERE receipt_id = ? AND kind = ?",
                              (receipt_id, INTENT)).fetchone()
        if intent is None:
            raise TamperRefused(f"No receipt {receipt_id}")
        if json.loads(intent["body"])["agent_run"].get("model") != DEMO_MODEL:
            raise TamperRefused(f"{receipt_id} is not a seeded scenario receipt")
        others = conn.execute("SELECT COUNT(*) FROM records WHERE seq > ? AND receipt_id != ?",
                              (intent["seq"], receipt_id)).fetchone()[0]
        if others:
            raise TamperRefused(f"Records of other receipts follow {receipt_id}; rewriting would change their hashes")

        before = conn.execute("SELECT hash FROM records WHERE seq < ? ORDER BY seq DESC LIMIT 1",
                              (intent["seq"],)).fetchone()
        prev = before["hash"] if before else GENESIS_HASH
        old_hash = intent["hash"]
        conn.execute("DROP TRIGGER IF EXISTS records_no_update")
        conn.execute("DROP TRIGGER IF EXISTS records_no_delete")
        new_hash = None
        for row in conn.execute("SELECT seq, kind, body FROM records WHERE seq >= ? ORDER BY seq",
                                (intent["seq"],)).fetchall():
            body = json.loads(row["body"])
            if row["kind"] == INTENT:
                edit(body)
            elif row["kind"] == "paypal_order_created" and new_hash:
                body["data"]["invoice_id"] = new_hash  # cover the tracks inside the ledger
            body["prev_hash"] = prev
            prev = record_hash(body)
            if row["kind"] == INTENT:
                new_hash = prev
            conn.execute("UPDATE records SET body = ?, hash = ? WHERE seq = ?", (canonical_json(body), prev, row["seq"]))
        for sql in _TRIGGERS:
            conn.execute(sql)
    return {"old_hash": old_hash, "new_hash": new_hash}
