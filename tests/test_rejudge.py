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
    assert payload["buyer_messages"] == ["no"] and NOTES in payload["disputed_item_notes"]


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
