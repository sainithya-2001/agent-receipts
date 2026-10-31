"""The hash-chained ledger: intents first, then PayPal events linked to them.

Every record (intent or event) is one link in a single chain. Its hash is the SHA-256 of
the canonical JSON of the record, and the record includes the previous record's hash, so
changing, removing or reordering any record breaks every hash after it.

An intent is never changed once logged. PayPal IDs arrive later as event records that
point at the receipt_id.
"""
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from backend.ledger import store
from backend.ledger.models import RECEIPT_ID_PATTERN, AgentRun, Mandate, PayPalRefs

GENESIS_HASH = "0" * 64
INTENT = "intent"
# Event fields get_receipt adds itself, so event data can't use them.
_RESERVED_EVENT_KEYS = {"kind", "at", "seq", "prev_hash", "hash"}


class LedgerError(RuntimeError):
    pass


class InvalidReceipt(LedgerError):
    pass


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def record_hash(body: dict[str, Any]) -> str:
    """body is the record including prev_hash and excluding hash."""
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(receipt_id: str, kind: str, order_id: str | None, body: dict[str, Any]) -> dict[str, Any]:
    """Link body after the current head. Returns the stored record with prev_hash and hash."""
    with store.write() as conn:
        head = conn.execute("SELECT hash FROM records ORDER BY seq DESC LIMIT 1").fetchone()
        if kind != INTENT and conn.execute(
            "SELECT 1 FROM records WHERE receipt_id = ? AND kind = ?", (receipt_id, INTENT)
        ).fetchone() is None:
            raise KeyError(receipt_id)
        body = {**body, "prev_hash": head["hash"] if head else GENESIS_HASH}
        digest = record_hash(body)
        try:
            cur = conn.execute(
                "INSERT INTO records (receipt_id, kind, order_id, body, hash) VALUES (?, ?, ?, ?, ?)",
                (receipt_id, kind, order_id, canonical_json(body), digest),
            )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"Could not append {kind} for {receipt_id}: {exc}") from exc
    return {"seq": cur.lastrowid, **body, "hash": digest}


def log_intent(
    mandate: dict[str, Any],
    agent_run: dict[str, Any],
    *,
    receipt_id: str | None = None,
    created_at: str | None = None,
) -> str:
    """Validate and hash the intent, append it to the chain, and return its receipt_id.

    receipt_id and created_at are only for replaying a saved receipt (see staged.py)."""
    try:
        mandate = Mandate.model_validate(mandate).model_dump(mode="json")
        agent_run = AgentRun.model_validate(agent_run).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidReceipt(f"Receipt rejected: {exc}") from exc
    if receipt_id is None:
        receipt_id = f"rcpt_{uuid.uuid4().hex[:16]}"
    elif not re.fullmatch(RECEIPT_ID_PATTERN, receipt_id):
        raise InvalidReceipt(f"Bad receipt_id {receipt_id!r}")
    body = {
        "receipt_id": receipt_id,
        "created_at": created_at or _now(),
        "mandate": mandate,
        "agent_run": agent_run,
        "paypal": PayPalRefs().model_dump(),
    }
    _append(receipt_id, INTENT, None, body)
    return receipt_id


def append_event(receipt_id: str, kind: str, data: dict[str, Any], *, at: str | None = None) -> dict[str, Any]:
    """Append an event linked to a logged receipt. Raises KeyError for an unknown receipt."""
    if kind == INTENT:
        raise LedgerError("'intent' is reserved for receipts")
    clash = _RESERVED_EVENT_KEYS & data.keys()
    if clash:
        raise LedgerError(f"Event data can't use reserved keys {sorted(clash)}")
    body = {"receipt_id": receipt_id, "kind": kind, "at": at or _now(), "data": data}
    return _flatten_event(_append(receipt_id, kind, data.get("order_id"), json.loads(canonical_json(body))))


def _flatten_event(record: dict[str, Any]) -> dict[str, Any]:
    return {"kind": record["kind"], "at": record["at"], **record["data"],
            "seq": record["seq"], "prev_hash": record["prev_hash"], "hash": record["hash"]}


def _rows_for(receipt_id: str) -> list[dict[str, Any]]:
    rows = store.read("SELECT seq, kind, body, hash FROM records WHERE receipt_id = ? ORDER BY seq", (receipt_id,))
    return [{"_kind": r["kind"], "seq": r["seq"], **json.loads(r["body"]), "hash": r["hash"]} for r in rows]


def get_receipt(receipt_id: str) -> dict[str, Any] | None:
    """The hashed intent plus its events, in chain order. None if not logged."""
    records = _rows_for(receipt_id)
    intents = [r for r in records if r.pop("_kind") == INTENT]
    if not intents:
        return None
    intent = intents[0]
    intent.pop("seq")
    events = [_flatten_event(r) for r in records if r is not intent]
    return {**intent, "events": events}


def find_by_order(order_id: str) -> str | None:
    rows = store.read("SELECT receipt_id FROM records WHERE order_id = ? ORDER BY seq LIMIT 1", (order_id,))
    return rows[0]["receipt_id"] if rows else None


def verify_chain() -> dict[str, Any]:
    """Walk the whole chain and recheck every link and hash.

    Returns {"ok", "records", "head_hash", "bad_seq", "error"}; bad_seq is the first record
    that fails."""
    rows = store.read("SELECT seq, receipt_id, kind, body, hash FROM records ORDER BY seq")
    prev = GENESIS_HASH
    seen_receipts: set[str] = set()

    def fail(seq: int, error: str) -> dict[str, Any]:
        return {"ok": False, "records": len(rows), "head_hash": rows[-1]["hash"], "bad_seq": seq, "error": error}

    for expected_seq, row in enumerate(rows, start=1):
        seq = row["seq"]
        if seq != expected_seq:
            return fail(seq, f"record {expected_seq} is missing")
        try:
            body = json.loads(row["body"])
        except ValueError:
            return fail(seq, "body is not valid JSON")
        if body.get("prev_hash") != prev:
            return fail(seq, "prev_hash does not match the previous record's hash")
        if record_hash(body) != row["hash"]:
            return fail(seq, "hash does not match the record's contents")
        if body.get("receipt_id") != row["receipt_id"]:
            return fail(seq, "receipt_id column does not match the record")
        if (row["kind"] == INTENT) != ("mandate" in body) or (row["kind"] != INTENT and body.get("kind") != row["kind"]):
            return fail(seq, "kind column does not match the record")
        if row["kind"] == INTENT:
            seen_receipts.add(row["receipt_id"])
        elif row["receipt_id"] not in seen_receipts:
            return fail(seq, "event appears before its receipt's intent")
        prev = row["hash"]
    return {"ok": True, "records": len(rows), "head_hash": prev, "bad_seq": None, "error": None}


def intent_hash(receipt_id: str) -> str | None:
    """Recompute the intent's hash from its stored contents (not the stored hash column)."""
    rows = store.read("SELECT body FROM records WHERE receipt_id = ? AND kind = ?", (receipt_id, INTENT))
    return record_hash(json.loads(rows[0]["body"])) if rows else None


def events_of_kinds(kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every event of the given kinds across all receipts, in chain order, with its receipt_id."""
    rows = store.read(
        f"SELECT seq, kind, body, hash FROM records WHERE kind IN ({','.join('?' * len(kinds))}) ORDER BY seq", kinds)
    return [{"receipt_id": json.loads(r["body"])["receipt_id"],
             **_flatten_event({"seq": r["seq"], **json.loads(r["body"]), "hash": r["hash"]})} for r in rows]
