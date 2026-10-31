import json
import sqlite3
from pathlib import Path

import pytest

from backend import ledger
from backend.ledger import chain, store
from backend.ledger.models import Receipt

MANDATE = {
    "request_text": "Buy me running shoes, size 8, under $120",
    "constraints": {"category": "running shoes", "size": "8", "max_price": 120, "currency": "USD"},
}


def agent_run(**overrides):
    run = {
        "model": "m",
        "options_considered": [{"product_id": "p_03", "price": 109.99, "attrs": {"size": "8"}, "reason": "fits"}],
        "chosen_product_id": "p_03",
        "chosen_size": "8",
        "reasoning": "Cushioned, under budget.",
        "steps": [{"tool": "search_products", "input": {"size": "8"}, "result_ids": ["p_03"]}],
    }
    return {**run, **overrides}


def tamper(sql, params=()):
    """What someone editing the database file directly could do: drop the guards and write."""
    conn = store.connection()
    conn.executescript("DROP TRIGGER records_no_update; DROP TRIGGER records_no_delete;")
    conn.execute(sql, params)


def test_log_and_get_receipt_matches_schema():
    rid = ledger.log_intent(MANDATE, agent_run())
    receipt = ledger.get_receipt(rid)

    Receipt.model_validate(receipt)
    assert receipt["receipt_id"] == rid
    assert receipt["mandate"]["constraints"]["max_price"] == 120.0
    assert receipt["agent_run"]["steps"][0]["tool"] == "search_products"  # extra keys kept
    assert receipt["paypal"] == {"order_id": None, "capture_id": None, "amount": None}
    assert receipt["prev_hash"] == ledger.GENESIS_HASH
    body = {k: v for k, v in receipt.items() if k not in ("hash", "events")}
    assert receipt["hash"] == chain.record_hash(body)
    assert receipt["events"] == []


def test_records_chain_onto_each_other():
    a = ledger.log_intent(MANDATE, agent_run())
    event = ledger.append_event(a, "paypal_order_created", {"order_id": "ORD1"})
    b = ledger.log_intent(MANDATE, agent_run())

    assert event["prev_hash"] == ledger.get_receipt(a)["hash"]
    assert ledger.get_receipt(b)["prev_hash"] == event["hash"]
    assert ledger.verify_chain() == {"ok": True, "records": 3, "head_hash": ledger.get_receipt(b)["hash"],
                                     "bad_seq": None, "error": None}


def test_empty_chain_verifies():
    assert ledger.verify_chain()["ok"] is True


def test_events_do_not_change_the_intent():
    rid = ledger.log_intent(MANDATE, agent_run())
    before = ledger.get_receipt(rid)
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1", "status": "CREATED"})
    ledger.append_event(rid, "paypal_captured", {"order_id": "ORD1", "capture_id": "CAP1"})
    after = ledger.get_receipt(rid)

    assert {k: v for k, v in after.items() if k != "events"} == {k: v for k, v in before.items() if k != "events"}
    assert [e["kind"] for e in after["events"]] == ["paypal_order_created", "paypal_captured"]
    assert after["events"][1]["capture_id"] == "CAP1"
    assert ledger.find_by_order("ORD1") == rid
    assert ledger.find_by_order("ORD_NOPE") is None


def test_records_are_append_only():
    ledger.log_intent(MANDATE, agent_run())
    conn = store.connection()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE records SET body = '{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM records")


def test_verify_catches_edited_record():
    rid = ledger.log_intent(MANDATE, agent_run())
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1"})
    body = json.loads(store.read("SELECT body FROM records WHERE seq = 1")[0]["body"])
    body["mandate"]["constraints"]["max_price"] = 500.0  # "the buyer said $500"
    tamper("UPDATE records SET body = ? WHERE seq = 1", (chain.canonical_json(body),))

    result = ledger.verify_chain()
    assert result["ok"] is False and result["bad_seq"] == 1 and "hash" in result["error"]


def test_verify_catches_rehashed_record():
    """Recomputing the edited record's own hash doesn't help: the next record still points at the old one."""
    rid = ledger.log_intent(MANDATE, agent_run())
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1"})
    body = json.loads(store.read("SELECT body FROM records WHERE seq = 1")[0]["body"])
    body["mandate"]["constraints"]["size"] = "9"
    tamper("UPDATE records SET body = ?, hash = ? WHERE seq = 1", (chain.canonical_json(body), chain.record_hash(body)))

    result = ledger.verify_chain()
    assert result["ok"] is False and result["bad_seq"] == 2 and "prev_hash" in result["error"]


def test_verify_catches_deleted_record():
    rid = ledger.log_intent(MANDATE, agent_run())
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1"})
    ledger.append_event(rid, "paypal_captured", {"order_id": "ORD1"})
    tamper("DELETE FROM records WHERE seq = 2")

    result = ledger.verify_chain()
    assert result["ok"] is False and result["bad_seq"] == 3


@pytest.mark.parametrize("mandate, run", [
    ({**MANDATE, "request_text": ""}, agent_run()),
    ({"request_text": "shoes"}, agent_run()),
    ({**MANDATE, "constraints": {**MANDATE["constraints"], "max_price": -1}}, agent_run()),
    (MANDATE, agent_run(model=None)),
    (MANDATE, agent_run(options_considered=[{"product_id": "p_03"}])),
    (MANDATE, {k: v for k, v in agent_run().items() if k != "reasoning"}),
])
def test_invalid_receipts_are_rejected(mandate, run):
    with pytest.raises(ledger.InvalidReceipt):
        ledger.log_intent(mandate, run)
    assert ledger.verify_chain()["records"] == 0


def test_event_rules():
    with pytest.raises(KeyError):
        ledger.append_event("rcpt_0000000000000000", "paypal_captured", {})
    rid = ledger.log_intent(MANDATE, agent_run())
    with pytest.raises(ledger.LedgerError):
        ledger.append_event(rid, "intent", {})
    with pytest.raises(ledger.LedgerError):
        ledger.append_event(rid, "paypal_captured", {"hash": "x"})


def test_explicit_receipt_id_rules():
    ledger.log_intent(MANDATE, agent_run(), receipt_id="rcpt_0123456789abcdef")
    with pytest.raises(ledger.LedgerError):
        ledger.log_intent(MANDATE, agent_run(), receipt_id="rcpt_0123456789abcdef")
    with pytest.raises(ledger.InvalidReceipt):
        ledger.log_intent(MANDATE, agent_run(), receipt_id="not_a_receipt")
    result = ledger.verify_chain()
    assert result["ok"] is True and result["records"] == 1


def test_load_staged_receipt_for_prestaged_dispute():
    assert ledger.load_staged() == ["rcpt_627e333bfb7045c1"]
    receipt = ledger.get_receipt("rcpt_627e333bfb7045c1")

    Receipt.model_validate(receipt)
    assert receipt["created_at"] == "2026-10-30T18:49:21+00:00"
    assert receipt["mandate"]["constraints"]["size"] == "8"
    assert receipt["agent_run"]["chosen_product_id"] == "p_03"
    assert [e["kind"] for e in receipt["events"]] == ["paypal_order_created", "paypal_captured", "ledger_import"]
    assert receipt["events"][1]["capture_id"] == "9V56960783661725H"
    assert ledger.find_by_order("3DS712773U002684V") == "rcpt_627e333bfb7045c1"
    assert ledger.verify_chain()["ok"] is True

    assert ledger.load_staged() == []  # idempotent
    assert ledger.verify_chain()["records"] == 4


def test_file_ledger_survives_restart(tmp_path):
    ledger.configure(f"sqlite:///{tmp_path / 'ledger.db'}")
    rid = ledger.log_intent(MANDATE, agent_run())
    ledger.configure(f"sqlite:///{tmp_path / 'ledger.db'}")  # reconnect, as a new process would
    assert ledger.get_receipt(rid)["receipt_id"] == rid
    assert ledger.verify_chain()["ok"] is True


def test_relative_sqlite_paths_resolve_to_repo_root():
    repo_root = Path(store.__file__).resolve().parents[2]
    assert Path(store.sqlite_path("sqlite:///./agent_receipts.db")) == repo_root / "agent_receipts.db"
    with pytest.raises(ValueError):
        store.sqlite_path("postgresql://localhost/db")
