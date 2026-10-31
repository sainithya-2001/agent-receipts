"""Replay saved receipts from scenarios/staged/ into the ledger under their original IDs.

The pre-staged dispute PP-R-LQT-10195344 carries custom = rcpt_627e333bfb7045c1, so that
receipt must exist in every ledger (local or hosted) before the dispute webhook arrives.
Loading is idempotent: receipts already in the ledger are skipped.
"""
import json
from pathlib import Path

from backend.ledger.chain import LedgerError, append_event, get_receipt, log_intent

STAGED_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "staged"
_ROOT = Path(__file__).resolve().parents[2]


def load_staged(directory: Path = STAGED_DIR) -> list[str]:
    """Returns the receipt_ids loaded this call."""
    loaded = []
    for path in sorted(directory.glob("rcpt_*.json")):
        saved = json.loads(path.read_text("utf-8"))
        receipt_id = saved["receipt_id"]
        if get_receipt(receipt_id) is not None:
            continue
        try:
            log_intent(saved["mandate"], saved["agent_run"], receipt_id=receipt_id, created_at=saved["created_at"])
        except LedgerError:
            # Another process (API or MCP server) loaded it first.
            if get_receipt(receipt_id) is not None:
                continue
            raise
        checkout, times = saved["checkout"], saved["paypal_times"]
        # The same events checkout.start() writes for a vaulted order.
        append_event(receipt_id, "paypal_order_created",
                     {"order_id": checkout["order_id"], "status": checkout["status"], "amount": checkout["amount"],
                      "currency": checkout["currency"], "vaulted": True},
                     at=times["order_created"])
        if checkout.get("capture_id"):
            append_event(receipt_id, "paypal_captured",
                         {"order_id": checkout["order_id"], "status": checkout["status"],
                          "capture_id": checkout["capture_id"]},
                         at=times["captured"])
        source = path.relative_to(_ROOT).as_posix() if path.is_relative_to(_ROOT) else path.name
        append_event(receipt_id, "ledger_import", {"source": source, "note": saved.get("_note", "")})
        loaded.append(receipt_id)
    return loaded
