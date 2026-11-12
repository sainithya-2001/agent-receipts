"""The buyer's complaint can sit in the disputed item's notes while the thread says "no" (PP-R-QHV-10199556),
and a verdict can be superseded by appending a new one."""
import json

import pytest

from backend import ledger, resolve
from backend.agents import adjudicator
from tests.test_disputes_flow import fake_claude

STAGED = "rcpt_627e333bfb7045c1"
NOTES = ("I ordered running shoes in size 8 through an automated shopping assistant, but the pair I received "
         "is size 9. The item is not what I ordered, so I would like a full refund of $109.99.")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("FILING_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setattr(resolve, "_anchor", lambda *a, **k: None)
    ledger.load_staged()


def qhv_dispute():
    """The shape of PP-R-QHV-10199556: thread message "no", the complaint in the item's notes."""
    return {"dispute_id": "PP-R-QHV-10199556", "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            "status": "UNDER_REVIEW", "dispute_life_cycle_stage": "INQUIRY",
            "dispute_amount": {"currency_code": "USD", "value": "109.99"},
            "disputed_transactions": [{"seller_transaction_id": None, "custom": STAGED,
                                       "gross_amount": {"currency_code": "USD", "value": "109.99"},
                                       "items": [{"item_name": "Running shoes, size 8",
                                                  "item_description": "Running shoes, size 8", "notes": NOTES,
                                                  "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED"}]}],
            "messages": [{"posted_by": "BUYER", "content": "no"}], "links": [{"rel": "self"}]}


def test_delivered_report_reads_item_notes_and_messages_together():
    d = qhv_dispute()
    assert adjudicator.delivered_report(adjudicator.buyer_messages(d)) == {}  # the thread alone says nothing
    assert adjudicator.buyer_statements(d)[0] == "no" and NOTES in adjudicator.buyer_statements(d)
    assert adjudicator.delivered_report(adjudicator.buyer_statements(d)) == {"size": "9"}


def test_message_no_with_size_complaint_in_notes_is_merchant_error_al4():
    claude = fake_claude("MERCHANT_ERROR", "AL-4")
    result = resolve.resolve(qhv_dispute(), client=claude)
    v = result["verdict"]
    assert v["fault_class"] == "MERCHANT_ERROR" and v["cited_rule"]["id"] == "AL-4" and v["override"] is None
    size = next(r for r in v["diff"] if r["field"] == "size")
    assert size["delivered"] == "9" and size["delivered_ok"] is False
    payload = json.loads(claude.messages.create.call_args_list[0].kwargs["messages"][0]["content"])
    assert payload["untrusted_buyer_text"]["buyer_messages"] == ["no"]
    assert NOTES in payload["untrusted_buyer_text"]["disputed_item_notes"]


def test_rejudge_appends_a_verdict_that_supersedes_and_keeps_the_old_one():
    first = resolve.resolve(qhv_dispute(), client=fake_claude("BUYER_REMORSE", "AL-5"))  # the old, wrong verdict
    assert first["verdict"]["fault_class"] == "BUYER_REMORSE"
    old = [e for e in ledger.get_receipt(STAGED)["events"] if e["kind"] == "dispute_verdict"][-1]

    with pytest.raises(resolve.ResolveError):
        resolve.rejudge(qhv_dispute(), "  ", client=fake_claude())
    again = resolve.rejudge(qhv_dispute(), "Item notes carry the size complaint",
                            client=fake_claude("MERCHANT_ERROR", "AL-4"))
    assert again["verdict"]["fault_class"] == "MERCHANT_ERROR" and again["filed"] is None

    events = [e for e in ledger.get_receipt(STAGED)["events"] if e["kind"] == "dispute_verdict"]
    assert len(events) == 2 and events[0]["seq"] == old["seq"]  # the old verdict is still there, unchanged
    assert events[0]["verdict"]["fault_class"] == "BUYER_REMORSE" and "supersedes" not in events[0]
    assert events[1]["supersedes"] == old["seq"] and events[1]["reason"] == "Item notes carry the size complaint"
    assert events[1]["verdict"]["rejudged"] == {"supersedes": old["seq"], "reason": "Item notes carry the size complaint"}
    assert ledger.verify_chain()["ok"] is True

    # later runs read the latest verdict and don't judge again
    claude = fake_claude("BUYER_REMORSE", "AL-5")
    assert resolve.resolve(qhv_dispute(), client=claude)["verdict"]["fault_class"] == "MERCHANT_ERROR"
    claude.messages.create.assert_not_called()


def test_rejudge_needs_an_existing_verdict():
    with pytest.raises(resolve.ResolveError):
        resolve.rejudge(qhv_dispute(), "why", client=fake_claude())


def test_queue_shows_the_latest_verdict():
    resolve.resolve(qhv_dispute(), client=fake_claude("BUYER_REMORSE", "AL-5"))
    resolve.rejudge(qhv_dispute(), "notes", client=fake_claude("MERCHANT_ERROR", "AL-4"))
    row = next(r for r in resolve.queue()["items"] if r["dispute_id"] == "PP-R-QHV-10199556")
    assert row["verdict"]["fault_class"] == "MERCHANT_ERROR" and row["verdict"]["cited_rule"] == "AL-4"


# --- paypal_outcome: read-only record of what PayPal decided ---

def resolved(d):
    return {**d, "status": "RESOLVED", "update_time": "2026-11-11T20:16:30.000Z",
            "dispute_outcome": {"outcome_code": "RESOLVED_BUYER_FAVOUR", "outcome_reason": "SELLER_ISSUED_REFUND",
                                "amount_refunded": {"currency_code": "USD", "value": "109.99"}},
            "refund_details": {"transactions": [{"id": "0KB87031KB858403M"}]}}


def outcomes():
    return [e for e in ledger.get_receipt(STAGED)["events"] if e["kind"] == "paypal_outcome"]


def test_resolved_dispute_records_one_paypal_outcome_event():
    resolve.resolve(qhv_dispute(), client=fake_claude("MERCHANT_ERROR", "AL-4"))
    assert outcomes() == []  # not resolved yet: nothing recorded
    resolve.resolve(resolved(qhv_dispute()), client=fake_claude())
    resolve.resolve(resolved(qhv_dispute()), client=fake_claude())  # again: still one
    (event,) = outcomes()
    assert event["dispute_id"] == "PP-R-QHV-10199556" and event["outcome_code"] == "RESOLVED_BUYER_FAVOUR"
    assert event["outcome_reason"] == "SELLER_ISSUED_REFUND" and event["refund_id"] == "0KB87031KB858403M"
    assert event["amount_refunded"] == {"currency_code": "USD", "value": "109.99"}
    assert event["resolved_at"] == "2026-11-11T20:16:30.000Z"
    assert event["item_notes"] == [NOTES]  # the notes only, not the listing description
    assert ledger.verify_chain()["ok"] is True


def test_simulated_disputes_never_get_an_outcome():
    resolve.resolve({**resolved(qhv_dispute()), "simulated": True}, client=fake_claude("MERCHANT_ERROR", "AL-4"))
    assert outcomes() == []


def test_late_refund_details_are_filled_in_by_a_later_resolve_and_then_stop():
    bare = {**qhv_dispute(), "status": "RESOLVED", "update_time": "2026-11-11T20:16:30.000Z",
            "dispute_outcome": {"outcome_code": "RESOLVED_BUYER_FAVOUR"}}  # refund details not there yet
    resolve.resolve(bare, client=fake_claude("MERCHANT_ERROR", "AL-4"))
    resolve.resolve(bare, client=fake_claude())  # nothing new: no extra event
    assert len(outcomes()) == 1 and outcomes()[0]["refund_id"] is None
    first = outcomes()[0]

    resolve.resolve(resolved(qhv_dispute()), client=fake_claude())  # PayPal now reports the refund
    resolve.resolve(resolved(qhv_dispute()), client=fake_claude())  # and again: still just one fill-in
    first_seen, filled = outcomes()
    assert first_seen == first  # never rewritten
    assert filled["supersedes"] == first["seq"] and set(filled["filled"]) == {"outcome_reason", "amount_refunded", "refund_id"}
    assert filled["refund_id"] == "0KB87031KB858403M" and filled["outcome_code"] == "RESOLVED_BUYER_FAVOUR"
    assert ledger.verify_chain()["ok"] is True


def _filed_dispute():
    resolve.resolve(qhv_dispute(), client=fake_claude("MERCHANT_ERROR", "AL-4"))
    ledger.append_event(STAGED, "dispute_response", {"dispute_id": "PP-R-QHV-10199556", "action": "accept_claim",
                                                     "note": "n"})


def test_rejudge_is_refused_after_a_response_was_filed():
    _filed_dispute()
    with pytest.raises(resolve.ResolveError, match="already filed"):
        resolve.rejudge(qhv_dispute(), "second thoughts", client=fake_claude("BUYER_REMORSE", "AL-5"))
    assert len([e for e in ledger.get_receipt(STAGED)["events"] if e["kind"] == "dispute_verdict"]) == 1


def test_forced_rejudge_after_filing_is_recorded_as_forced_and_still_never_files():
    _filed_dispute()
    out = resolve.rejudge(qhv_dispute(), "Operator override", client=fake_claude("BUYER_REMORSE", "AL-5"), force=True)
    assert out["filed"] is None
    event = [e for e in ledger.get_receipt(STAGED)["events"] if e["kind"] == "dispute_verdict"][-1]
    assert event["forced_after_filing"] == ["accept_claim"] and event["reason"] == "Operator override"
    assert event["verdict"]["rejudged"]["forced_after_filing"] == ["accept_claim"]
    with pytest.raises(resolve.ResolveError, match="reason"):
        resolve.rejudge(qhv_dispute(), " ", client=fake_claude(), force=True)
