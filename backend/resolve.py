"""The dispute flow end to end: dispute -> receipt -> verdict -> PayPal response.

The dispute's custom field is the receipt_id and its seller_transaction_id is the capture,
so the receipt and its PayPal anchor are found from the dispute alone. Each stage is
appended to the receipt as an event (dispute_opened, dispute_verdict, dispute_response),
which makes the flow safe to re-run on every webhook: a dispute is adjudicated once, and
each PayPal step is filed once.

Filing is off unless asked for (file=True), so looking at a real dispute never changes it.
"""
import logging
from typing import Any

import anthropic

from backend import ledger
from backend.agents import adjudicator, evidence
from backend.agents.evidence import NEEDS_NOTE
from backend.config import get_settings
from backend.paypal import disputes
from backend.paypal.client import PayPalClient

log = logging.getLogger(__name__)


class ResolveError(RuntimeError):
    pass


class FilingDisabled(ResolveError):
    """FILING_ENABLED is off, so nothing may be filed with PayPal."""


def check_filing_enabled() -> None:
    if not get_settings().filing_enabled:
        raise FilingDisabled("PayPal filing is disabled (FILING_ENABLED=false)")


def _events(receipt: dict[str, Any], kind: str, dispute_id: str) -> list[dict[str, Any]]:
    return [e for e in receipt["events"] if e["kind"] == kind and e.get("dispute_id") == dispute_id]


def _anchor(receipt_id: str, txn: dict[str, Any], paypal: PayPalClient | None, *,
            simulated: bool) -> dict[str, Any] | None:
    """The PayPal anchor check for a dispute. Fails closed for real disputes: with no capture id,
    no client, or a failed lookup, the result says so ({"unavailable": reason}) rather than
    passing as unanchored. Only an order that exists but has no invoice_id is unanchored."""
    capture_id = txn.get("seller_transaction_id")
    if simulated and (paypal is None or not capture_id):
        return None  # seeded scenario without a real order: nothing to check
    if not capture_id:
        return {"unavailable": "the dispute names no capture id (seller_transaction_id)"}
    if paypal is None:
        return {"unavailable": "no PayPal client configured"}
    try:
        return ledger.verify_receipt_against_paypal(receipt_id, paypal, capture_id=capture_id)
    except Exception as exc:  # PayPalError, network errors, malformed responses
        log.warning("anchor check for %s failed: %s", receipt_id, exc)
        return {"unavailable": f"capture lookup failed ({exc})"}


def resolve(dispute: dict[str, Any], *, paypal: PayPalClient | None = None, file: bool = False,
            client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    """Adjudicate a dispute (once) and, with file=True, take the next PayPal step.

    paypal=None skips the anchor check and can't file: for seeded scenarios."""
    if file:
        check_filing_enabled()
        if paypal is None or dispute.get("simulated"):
            raise ResolveError("Only real PayPal disputes can be filed")
    dispute_id = dispute["dispute_id"]
    txn = (dispute.get("disputed_transactions") or [{}])[0]
    receipt_id = txn.get("custom")
    receipt = ledger.get_receipt(receipt_id) if receipt_id else None
    if receipt is None:
        return {"dispute_id": dispute_id, "receipt_id": receipt_id, "status": "no_receipt",
                "why": "The disputed transaction carries no receipt_id we have in the ledger."}

    if not _events(receipt, "dispute_opened", dispute_id):
        ledger.append_event(receipt_id, "dispute_opened", {
            "dispute_id": dispute_id, "reason": dispute.get("reason"), "status": dispute.get("status"),
            "stage": dispute.get("dispute_life_cycle_stage"), "amount": dispute.get("dispute_amount"),
            "buyer_messages": adjudicator.buyer_messages(dispute), "simulated": bool(dispute.get("simulated")),
        })

    recorded = _events(receipt, "dispute_verdict", dispute_id)
    # A verdict is decided once, except one that only failed because PayPal couldn't be reached:
    # that is re-checked on the next run instead of being stuck in manual review.
    if recorded and recorded[-1]["verdict"].get("integrity", {}).get("check") != "anchor_unavailable":
        verdict = recorded[-1]["verdict"]
    else:
        anchor = _anchor(receipt_id, txn, paypal, simulated=bool(dispute.get("simulated")))
        facts = adjudicator.find_facts(receipt, dispute, chain=ledger.verify_chain(), anchor=anchor)
        verdict = adjudicator.adjudicate(facts, dispute, client=client)
        ledger.append_event(receipt_id, "dispute_verdict", {"dispute_id": dispute_id, "verdict": verdict})

    done = {e["action"] for e in _events(receipt, "dispute_response", dispute_id)}
    step = evidence.plan(dispute, verdict, done)
    result: dict[str, Any] = {"dispute_id": dispute_id, "receipt_id": receipt_id, "status": "adjudicated",
                              "verdict": verdict, "next": step, "filed": None,
                              "filing_enabled": get_settings().filing_enabled}
    if not (file and step["action"]):
        return result

    action = step["action"]
    note = evidence.write_note(action, verdict, receipt, dispute, client=client) if action in NEEDS_NOTE else None
    evidence.execute(paypal, dispute_id, action, note)
    ledger.append_event(receipt_id, "dispute_response", {"dispute_id": dispute_id, "action": action, "note": note})
    log.info("dispute %s: filed %s", dispute_id, action)
    return {**result, "status": "filed", "filed": {"action": action, "note": note}}


def resolve_by_id(paypal: PayPalClient, dispute_id: str, *, file: bool = False,
                  client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    return resolve(disputes.get_dispute(paypal, dispute_id), paypal=paypal, file=file, client=client)


# dispute_id -> the receipt_id in its custom field (None if none). custom never changes.
_receipt_of_dispute: dict[str, str | None] = {}


def _receipt_for(paypal: PayPalClient, dispute_id: str) -> str | None:
    if dispute_id not in _receipt_of_dispute:
        txn = (disputes.get_dispute(paypal, dispute_id).get("disputed_transactions") or [{}])[0]
        _receipt_of_dispute[dispute_id] = txn.get("custom")
    return _receipt_of_dispute[dispute_id]


def queue(paypal: PayPalClient | None = None) -> dict[str, Any]:
    """The merchant's dispute queue: every dispute with a receipt in the ledger, newest first.

    With paypal, live status and stage come from PayPal's list, and PayPal disputes the ledger
    hasn't seen yet are added (no verdict) if their custom field names a receipt we hold.
    Disputes on payments without a receipt (e.g. made before the ledger) are left out."""
    rows: dict[str, dict[str, Any]] = {}
    for e in ledger.events_of_kinds(("dispute_opened", "dispute_verdict", "dispute_response")):
        row = rows.setdefault(e["dispute_id"], {"dispute_id": e["dispute_id"], "receipt_id": e["receipt_id"],
                                                "filed": [], "verdict": None})
        if e["kind"] == "dispute_opened":
            amount = e.get("amount") or {}
            row.update(opened_at=e["at"], reason=e.get("reason"), status=e.get("status"), stage=e.get("stage"),
                       amount=amount.get("value"), currency=amount.get("currency_code"),
                       simulated=e.get("simulated", False))
        elif e["kind"] == "dispute_verdict":
            v = e["verdict"]
            row["verdict"] = {k: v.get(k) for k in ("status", "fault_class", "confidence", "agent_flagged", "violations")}
            row["verdict"]["cited_rule"] = (v.get("cited_rule") or {}).get("id")
            row["verdict"]["integrity"] = (v.get("integrity") or {}).get("status")
            row["verdict"]["integrity_check"] = (v.get("integrity") or {}).get("check")
        else:
            row["filed"].append(e["action"])

    paypal_error = None
    if paypal is not None:
        try:
            items = disputes.list_disputes(paypal).get("items", [])
        except Exception as exc:  # the queue still shows the ledger's view
            log.warning("dispute list failed: %s", exc)
            items, paypal_error = [], str(exc)
        for d in items:
            row = rows.get(d["dispute_id"])
            if row is None:
                try:
                    receipt_id = _receipt_for(paypal, d["dispute_id"])
                except Exception as exc:
                    log.warning("dispute %s lookup failed: %s", d["dispute_id"], exc)
                    continue
                if not receipt_id or ledger.get_receipt(receipt_id) is None:
                    continue
                amount = d.get("dispute_amount") or {}
                row = rows[d["dispute_id"]] = {
                    "dispute_id": d["dispute_id"], "receipt_id": receipt_id, "filed": [], "verdict": None,
                    "opened_at": d.get("create_time"), "reason": d.get("reason"), "simulated": False,
                    "amount": amount.get("value"), "currency": amount.get("currency_code")}
            row.update(status=d.get("status"), stage=d.get("dispute_life_cycle_stage"), live=True)
    items = sorted(rows.values(), key=lambda r: r.get("opened_at") or "", reverse=True)
    return {"items": items, "paypal_error": paypal_error}
