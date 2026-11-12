"""Mandates: a request the buyer has read, confirmed, and may spend once.

POST /mandate/parse stores the parsed request here and returns a mandate_id. POST /mandate/{id}/confirm
stamps the server's time and locks the constraints. The public POST /shop accepts only the id of a
confirmed, unused mandate and uses it up, so what the receipt records as "confirmed by buyer" was
actually confirmed through the API.

Stored in the ledger's database (a `mandates` table outside the hash chain: bookkeeping, not evidence),
so they survive a worker restart as far as the database does (the free Render plan has no disk, so they
still reset on redeploy). A run that fails before any PayPal order can exist gives its mandate back
(release); one that may have reached PayPal keeps it used.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.ledger import store


class MandateNotFound(LookupError):
    pass


class MandateNotConfirmed(RuntimeError):
    pass


class MandateUsed(RuntimeError):
    pass


def _public(row: Any) -> dict[str, Any]:
    return {"mandate_id": row["mandate_id"], "request_text": row["request_text"],
            "constraints": json.loads(row["constraints"]), "confirmed_by_buyer_at": row["confirmed_by_buyer_at"],
            "used": bool(row["used"])}


def _get(mandate_id: str) -> Any:
    rows = store.read("SELECT * FROM mandates WHERE mandate_id = ?", (mandate_id,))
    if not rows:
        raise MandateNotFound(f"No such mandate: {mandate_id} (mandates reset when the hosted database does)")
    return rows[0]


def create(request_text: str, constraints: dict[str, Any]) -> dict[str, Any]:
    mandate_id = f"mnd_{uuid.uuid4().hex[:16]}"
    with store.write() as conn:
        conn.execute("INSERT INTO mandates (mandate_id, request_text, constraints) VALUES (?, ?, ?)",
                     (mandate_id, request_text, json.dumps(constraints)))
    return _public(_get(mandate_id))


def confirm(mandate_id: str) -> dict[str, Any]:
    """Record the buyer's confirmation with the server's time. Confirming again changes nothing."""
    _get(mandate_id)
    with store.write() as conn:
        conn.execute("UPDATE mandates SET confirmed_by_buyer_at = ? WHERE mandate_id = ? AND confirmed_by_buyer_at IS NULL",
                     (datetime.now(timezone.utc).isoformat(), mandate_id))
    return _public(_get(mandate_id))


def claim(mandate_id: str) -> dict[str, Any]:
    """Use the mandate for one purchase: it must be confirmed and not used before."""
    with store.write() as conn:
        row = conn.execute("SELECT * FROM mandates WHERE mandate_id = ?", (mandate_id,)).fetchone()
        if row is None:
            raise MandateNotFound(f"No such mandate: {mandate_id} (mandates reset when the hosted database does)")
        if row["confirmed_by_buyer_at"] is None:
            raise MandateNotConfirmed("The buyer hasn't confirmed this mandate")
        if row["used"]:
            raise MandateUsed("This mandate has already been used for a purchase")
        conn.execute("UPDATE mandates SET used = 1 WHERE mandate_id = ?", (mandate_id,))
    return _public({**dict(row), "used": 1})


def release(mandate_id: str, *, order_may_exist: bool) -> bool:
    """Give a claimed mandate back after a run that failed. Refused (False) when a PayPal order may exist:
    a purchase that might have been charged must not be repeatable. The caller decides that from where
    the run failed (before the ordering step, or in checkout before any PayPal call)."""
    if order_may_exist:
        return False
    with store.write() as conn:
        return conn.execute("UPDATE mandates SET used = 0 WHERE mandate_id = ?", (mandate_id,)).rowcount == 1
