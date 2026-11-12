"""The dispute flow end to end: dispute -> receipt -> verdict -> PayPal response.

The dispute's custom field is the receipt_id and its seller_transaction_id is the capture,
so the receipt and its PayPal anchor are found from the dispute alone. Each stage is
appended to the receipt as an event (dispute_opened, dispute_verdict, dispute_response),
which makes the flow safe to re-run on every webhook: a dispute is adjudicated once, and
each PayPal step is filed once.

Filing is off unless asked for (file=True), so looking at a real dispute never changes it.
"""
import logging
import threading
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


OUTCOME_FIELDS = ("outcome_code", "outcome_reason", "amount_refunded", "refund_id", "resolved_at", "item_notes")


def _blank(value: Any) -> bool:
    return value in (None, "", [], {})


def _record_outcome(receipt: dict[str, Any], dispute: dict[str, Any]) -> None:
    """Once PayPal reports the dispute RESOLVED, append a read-only paypal_outcome event (what PayPal
    decided, the refund, when). Nothing is sent to PayPal. PayPal's refund details can lag the status
    change, so a later resolve (or webhook) that sees fields the recorded outcome lacks appends one more
    paypal_outcome that carries everything known and names the one it fills in (supersedes); nothing is
    ever rewritten, and once nothing is missing no more events are added."""
    if dispute.get("simulated") or dispute.get("status") != "RESOLVED":
        return
    dispute_id = dispute["dispute_id"]
    outcome = dispute.get("dispute_outcome") or {}
    refunds = (dispute.get("refund_details") or {}).get("transactions") or []
    seen = {
        "outcome_code": outcome.get("outcome_code"), "outcome_reason": outcome.get("outcome_reason"),
        "amount_refunded": outcome.get("amount_refunded"),
        "refund_id": refunds[0].get("id") if refunds else None, "resolved_at": dispute.get("update_time"),
        # the complaint as the dispute showed it, since PayPal keeps it in the item notes
        "item_notes": adjudicator.item_notes(dispute, descriptions=False),
    }
    existing = _events(receipt, "paypal_outcome", dispute_id)
    if not existing:
        ledger.append_event(receipt["receipt_id"], "paypal_outcome", {"dispute_id": dispute_id, **seen})
        return
    last = existing[-1]
    filled = [k for k in OUTCOME_FIELDS if _blank(last.get(k)) and not _blank(seen[k])]
    if filled:
        merged = {k: last.get(k) if not _blank(last.get(k)) else seen[k] for k in OUTCOME_FIELDS}
        ledger.append_event(receipt["receipt_id"], "paypal_outcome",
                            {"dispute_id": dispute_id, **merged, "supersedes": last["seq"], "filled": filled})


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


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _dispute_lock(dispute_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(dispute_id, threading.Lock())


def resolve(dispute: dict[str, Any], *, paypal: PayPalClient | None = None, file: bool = False,
            client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    """Adjudicate a dispute and (with file=True) take its next PayPal step, one caller at a time per
    dispute in this process: a webhook and a button press can't both decide or file the same step.
    See _resolve for the steps. (Across processes, PayPal's deterministic Request-Id does the same job.)"""
    with _dispute_lock(dispute["dispute_id"]):
        return _resolve(dispute, paypal=paypal, file=file, client=client)


def _resolve(dispute: dict[str, Any], *, paypal: PayPalClient | None = None, file: bool = False,
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
            "buyer_messages": adjudicator.buyer_messages(dispute),
            "item_notes": adjudicator.item_notes(dispute, descriptions=False),
            "simulated": bool(dispute.get("simulated")),
        })

    _record_outcome(receipt, dispute)
    receipt = ledger.get_receipt(receipt_id)  # includes anything just recorded
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
    receipt = ledger.get_receipt(receipt_id)  # the verdict event just recorded (or found) is in it
    verdict_event = _events(receipt, "dispute_verdict", dispute_id)[-1]
    anchor = evidence.anchor_fields(receipt, verdict_event, ledger.verify_chain()["head_hash"])
    note = evidence.write_note(action, verdict, receipt, dispute, client=client) if action in NEEDS_NOTE else None
    if note is not None:
        note = evidence.anchored_note(note, receipt_id, anchor)  # code appends the hashes; PayPal receives them
    evidence.execute(paypal, dispute_id, action, note)
    ledger.append_event(receipt_id, "dispute_response", {"dispute_id": dispute_id, "action": action, "note": note,
                                                         "anchor": anchor})
    log.info("dispute %s: filed %s", dispute_id, action)
    return {**result, "status": "filed", "filed": {"action": action, "note": note, "anchor": anchor}}


def rejudge(dispute: dict[str, Any], reason: str, *, paypal: PayPalClient | None = None,
            client: anthropic.Anthropic | None = None, force: bool = False) -> dict[str, Any]:
    """Judge a dispute again and append the new verdict as a dispute_verdict event with
    supersedes=<seq of the verdict it replaces> and the reason. The old verdict stays in the ledger;
    everything after this reads the latest. Never files anything.

    Once a response has been filed with PayPal the verdict it rested on is part of what PayPal holds, so a
    re-judgement is refused unless force=True (CLI --force) with a reason; the forced one is marked."""
    if not reason.strip():
        raise ResolveError("A re-judgement needs a reason")
    with _dispute_lock(dispute["dispute_id"]):
        dispute_id = dispute["dispute_id"]
        txn = (dispute.get("disputed_transactions") or [{}])[0]
        receipt = ledger.get_receipt(txn["custom"]) if txn.get("custom") else None
        recorded = _events(receipt, "dispute_verdict", dispute_id) if receipt else []
        if not recorded:
            raise ResolveError("There is no verdict to supersede; resolve the dispute first")
        old = recorded[-1]
        filed = [e["action"] for e in _events(receipt, "dispute_response", dispute_id)]
        if filed and not force:
            raise ResolveError(f"A response was already filed with PayPal ({', '.join(filed)}); "
                               "re-judging now would contradict it. Use --force with a reason to record a new verdict anyway.")
        anchor = _anchor(receipt["receipt_id"], txn, paypal, simulated=bool(dispute.get("simulated")))
        facts = adjudicator.find_facts(receipt, dispute, chain=ledger.verify_chain(), anchor=anchor)
        verdict = adjudicator.adjudicate(facts, dispute, client=client)
        verdict["rejudged"] = {"supersedes": old["seq"], "reason": reason}
        if filed:
            verdict["rejudged"]["forced_after_filing"] = filed
        ledger.append_event(receipt["receipt_id"], "dispute_verdict", {
            "dispute_id": dispute_id, "verdict": verdict, "supersedes": old["seq"], "reason": reason,
            **({"forced_after_filing": filed} if filed else {})})
        log.info("dispute %s: re-judged, supersedes verdict at chain #%s", dispute_id, old["seq"])
        return _resolve(dispute, paypal=paypal, file=False, client=client)


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
