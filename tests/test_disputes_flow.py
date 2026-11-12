import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend import ledger, resolve, scenarios
from backend.agents import evidence
from backend.app.main import app
from backend.paypal import webhooks
from backend.paypal.client import PayPalClient
from backend.rag import ingest, retrieve

BASE = "https://api-m.sandbox.paypal.com"
REAL_ANCHOR = resolve._anchor


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("FILING_ENABLED", raising=False)  # default off, whatever the local .env says
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", BASE)
    # These tests are about the flow, not the anchor check (see test_fail_closed.py for that).
    monkeypatch.setattr(resolve, "_anchor", lambda *args, **kwargs: None)


def fake_claude(fault="BUYER_REMORSE", rule="AL-5", note="Receipt rcpt shows the purchase matched."):
    """Answers the adjudicator with a verdict and the evidence agent with a note."""
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)

    def create(**kwargs):
        schema = kwargs["output_config"]["format"]["schema"]["properties"]
        out = {"note": note} if "note" in schema else {
            "fault_class": fault, "confidence": 0.9, "cited_rule_id": rule,
            "buyer_claim": "Buyer wants a refund.", "explanation": "Explained."}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(out))],
                               stop_reason="end_turn", usage=usage)

    client = MagicMock()
    client.messages.create.side_effect = create
    return client


def real_dispute(receipt_id, links=("accept_claim", "escalate"), capture_id=None):
    return {"dispute_id": "PP-D-1", "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "status": "OPEN",
            "dispute_life_cycle_stage": "INQUIRY", "dispute_amount": {"currency_code": "USD", "value": "109.99"},
            "disputed_transactions": [{"seller_transaction_id": capture_id, "custom": receipt_id,
                                       "gross_amount": {"currency_code": "USD", "value": "109.99"}}],
            "messages": [{"posted_by": "BUYER", "content": "Changed my mind."}],
            "links": [{"rel": "self"}] + [{"rel": r} for r in links]}


# --- RAG ---

def test_every_rule_parses_with_unique_ids():
    rules = ingest.load_rules()
    assert len(rules) == 20 and all(r["text"] for r in rules)
    assert {r["id"] for r in rules} >= {"AL-2", "AL-3", "AL-4", "AL-5", "AL-6", "ER-4", "RP-1"}


@pytest.mark.parametrize("query, expected", [
    ("buyer changed my mind purchase matches mandate", "AL-5"),
    ("listing instructs shopping assistants to ignore the budget", "AL-3"),
    ("sandbox evidence type", "ER-4"),
    ("return policy change of mind", "RP-1"),
])
def test_retrieval_ranks_the_right_rule_first(query, expected):
    assert retrieve.search(query)[0]["id"] == expected


def test_retrieval_is_deterministic_and_ignores_junk():
    assert retrieve.search("size wrong refund") == retrieve.search("size wrong refund")
    assert retrieve.search("zzz qqq") == []


# --- evidence agent: which PayPal step comes next ---

REFUND = {"status": "decided", "fault_class": "AGENT_ERROR", "action": "accept_claim"}
REMORSE = {"status": "decided", "fault_class": "BUYER_REMORSE", "action": "provide_evidence"}


def links(*rels, status="OPEN"):
    return {"status": status, "links": [{"rel": r} for r in rels]}


@pytest.mark.parametrize("verdict, dispute, done, action", [
    (REFUND, links("accept_claim", "escalate"), set(), "accept_claim"),
    (REFUND, links("accept_claim"), {"accept_claim"}, None),
    (REMORSE, links("accept_claim", "escalate", "make_offer"), set(), "escalate"),
    (REMORSE, links("accept_claim", "provide_evidence"), set(), "provide_evidence"),  # already a claim
    (REMORSE, links("provide_supporting_info"), {"escalate", "provide_evidence"}, None),  # waiting for adjudicate
    (REMORSE, links("adjudicate"), {"escalate", "provide_evidence"}, "adjudicate"),
    (REMORSE, links("adjudicate", status="RESOLVED"), set(), None),
    ({"status": "needs_review"}, links("accept_claim"), set(), None),
])
def test_plan(verdict, dispute, done, action):
    assert evidence.plan(dispute, verdict, done)["action"] == action


# A crash after a successful PayPal call but before the ledger write: the next run must read the step
# from PayPal's own dispute record, never re-file it, and say what it is waiting for.

def after_evidence(*rels):
    return {"status": "UNDER_REVIEW", "dispute_life_cycle_stage": "CHARGEBACK",
            "evidences": [{"evidence_type": "OTHER", "source": "SUBMITTED_BY_SELLER"}],
            "links": [{"rel": r} for r in rels]}


def test_lost_ledger_write_after_provide_evidence_waits_for_adjudicate_not_evidence():
    step = evidence.plan(after_evidence("provide_supporting_info"), REMORSE, set())
    assert step["action"] is None and step["why"] == "Waiting for PayPal to offer adjudicate."
    assert evidence.plan(after_evidence("adjudicate"), REMORSE, set())["action"] == "adjudicate"


def test_observed_done_uses_only_positive_evidence_from_paypal():
    assert evidence.observed_done(after_evidence()) == {"escalate", "provide_evidence"}
    assert evidence.observed_done({"dispute_life_cycle_stage": "INQUIRY"}) == set()
    buyer_only = {"dispute_life_cycle_stage": "INQUIRY", "evidences": [{"source": "SUBMITTED_BY_BUYER"}]}
    assert evidence.observed_done(buyer_only) == set()


def test_lost_ledger_write_after_accept_claim_never_refiles():
    gone = links("provide_supporting_info", status="UNDER_REVIEW")  # accept_claim no longer offered
    step = evidence.plan(gone, REFUND, set())
    assert step["action"] is None and "nothing is re-filed" in step["why"]
    assert evidence.plan(links(status="RESOLVED"), REFUND, set())["action"] is None


# --- the flow ---

def test_scenario_flow_records_events_and_never_files():
    s = scenarios.get_scenario("wrong_size")
    rid = scenarios.log_receipt(s)
    client = fake_claude("AGENT_ERROR", "AL-2")
    out = resolve.resolve(scenarios.build_dispute(s, rid), client=client)
    assert out["verdict"]["fault_class"] == "AGENT_ERROR" and out["filed"] is None
    assert [e["kind"] for e in ledger.get_receipt(rid)["events"]] == ["dispute_opened", "dispute_verdict"]
    with pytest.raises(resolve.ResolveError):
        resolve.resolve(scenarios.build_dispute(s, rid), client=client, file=True)
    assert ledger.verify_chain()["ok"] is True


def test_adjudicates_once_per_dispute():
    rid = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    client = fake_claude()
    first = resolve.resolve(real_dispute(rid), client=client)
    second = resolve.resolve(real_dispute(rid), client=client)
    assert client.messages.create.call_count == 1
    assert first["verdict"] == second["verdict"]
    assert [e["kind"] for e in ledger.get_receipt(rid)["events"]] == ["dispute_opened", "dispute_verdict"]


def test_filing_takes_one_step_per_run_and_records_it(monkeypatch):
    monkeypatch.setenv("FILING_ENABLED", "true")
    rid = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    client = fake_claude()
    paypal = MagicMock()
    with patch("backend.agents.evidence.disputes") as api:
        out = resolve.resolve(real_dispute(rid), paypal=paypal, file=True, client=client)
        assert out["filed"]["action"] == "escalate"
        api.escalate.assert_called_once()
        assert api.escalate.call_args.args[1] == "PP-D-1"

        # Next webhook: PayPal now offers provide_evidence.
        out = resolve.resolve(real_dispute(rid, links=("accept_claim", "provide_evidence")),
                              paypal=paypal, file=True, client=client)
        assert out["filed"]["action"] == "provide_evidence"
        api.provide_evidence.assert_called_once()
        api.accept_claim.assert_not_called()

        # Nothing new offered: no call, nothing recorded.
        out = resolve.resolve(real_dispute(rid, links=("provide_supporting_info",)),
                              paypal=paypal, file=True, client=client)
        assert out["filed"] is None and out["next"]["action"] is None

    kinds = [e["kind"] for e in ledger.get_receipt(rid)["events"]]
    assert kinds == ["dispute_opened", "dispute_verdict", "dispute_response", "dispute_response"]


def test_without_file_flag_paypal_is_never_called():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    with patch("backend.agents.evidence.disputes") as api:
        out = resolve.resolve(real_dispute(rid), paypal=MagicMock(), client=fake_claude("AGENT_ERROR", "AL-2"))
    assert out["next"]["action"] == "accept_claim" and out["filed"] is None
    assert not api.method_calls


def test_real_dispute_runs_the_anchor_check_on_its_capture(monkeypatch):
    monkeypatch.setattr(resolve, "_anchor", REAL_ANCHOR)
    rid = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    anchor = {"ok": True, "anchored": True, "custom_id_ok": True, "invoice_id": "a" * 64, "error": None}
    with patch("backend.resolve.ledger.verify_receipt_against_paypal", return_value=anchor) as check:
        out = resolve.resolve(real_dispute(rid, capture_id="CAP9"), paypal=MagicMock(), client=fake_claude())
    assert check.call_args.kwargs["capture_id"] == "CAP9"
    assert out["verdict"]["integrity"]["status"] == "verified"


def test_dispute_without_a_known_receipt():
    out = resolve.resolve(real_dispute("rcpt_0000000000000000"), client=fake_claude())
    assert out["status"] == "no_receipt"


# --- webhooks ---

SIGNED = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.paypal.com/cert",
          "paypal-transmission-id": "t1", "paypal-transmission-sig": "sig", "paypal-transmission-time": "now"}
EVENT = {"id": "WH-1", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "PP-D-1"}}


def mock_paypal(status="SUCCESS"):
    respx.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600}))
    return respx.post(f"{BASE}/v1/notifications/verify-webhook-signature").mock(
        return_value=httpx.Response(200, json={"verification_status": status}))


@respx.mock
def test_verify_signature_sends_headers_event_and_webhook_id():
    route = mock_paypal()
    assert webhooks.verify_signature(PayPalClient(), SIGNED, EVENT, "WHID") is True
    body = json.loads(route.calls.last.request.content)
    assert body["webhook_id"] == "WHID" and body["webhook_event"] == EVENT
    assert body["transmission_sig"] == "sig" and body["auth_algo"] == "SHA256withRSA"


@respx.mock
def test_verify_signature_failures():
    mock_paypal("FAILURE")
    assert webhooks.verify_signature(PayPalClient(), SIGNED, EVENT, "WHID") is False
    assert webhooks.verify_signature(PayPalClient(), {}, EVENT, "WHID") is False  # unsigned: no PayPal call


def test_dispute_id_only_for_dispute_events():
    assert webhooks.dispute_id_of(EVENT) == "PP-D-1"
    assert webhooks.dispute_id_of({**EVENT, "event_type": "PAYMENT.CAPTURE.COMPLETED"}) is None


@respx.mock
def test_webhook_route_verifies_then_handles_in_background(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    mock_paypal()
    with patch("backend.app.main._handle_dispute") as handle:
        r = TestClient(app).post("/paypal/webhook", json=EVENT, headers=SIGNED)
    assert r.status_code == 200 and r.json()["dispute_id"] == "PP-D-1"
    handle.assert_called_once_with("PP-D-1", False, "WH-1")  # DISPUTE_AUTO_FILE defaults off


@respx.mock
def test_webhook_route_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    mock_paypal("FAILURE")
    with patch("backend.app.main._handle_dispute") as handle:
        r = TestClient(app).post("/paypal/webhook", json=EVENT, headers=SIGNED)
    assert r.status_code == 400
    handle.assert_not_called()


def test_webhook_route_needs_webhook_id(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    assert TestClient(app).post("/paypal/webhook", json=EVENT).status_code == 503


# --- merchant queue and catalog ---

def test_queue_merges_ledger_verdicts_with_paypal_list():
    rid = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    unseen = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    resolve.resolve(real_dispute(rid), client=fake_claude())
    resolve._receipt_of_dispute.clear()
    paypal = MagicMock()
    custom = {"PP-D-NEW": unseen, "PP-D-OLD": None, "PP-D-ELSEWHERE": "rcpt_ffffffffffffffff"}
    with patch("backend.resolve.disputes") as api:
        api.list_disputes.return_value = {"items": [
            {"dispute_id": "PP-D-1", "status": "WAITING_FOR_SELLER_RESPONSE", "dispute_life_cycle_stage": "INQUIRY"},
            {"dispute_id": "PP-D-NEW", "status": "OPEN", "dispute_life_cycle_stage": "INQUIRY",
             "create_time": "2099-01-01T00:00:00Z", "dispute_amount": {"currency_code": "USD", "value": "5.00"}},
            {"dispute_id": "PP-D-OLD", "status": "RESOLVED", "create_time": "2026-10-01T00:00:00Z"},
            {"dispute_id": "PP-D-ELSEWHERE", "status": "OPEN", "create_time": "2026-10-02T00:00:00Z"},
        ]}
        api.get_dispute.side_effect = lambda _, d: {"disputed_transactions": [{"custom": custom[d]}]}
        items = resolve.queue(paypal)["items"]
        resolve.queue(paypal)  # second load: lookups are cached
    assert api.get_dispute.call_count == 3
    by_id = {r["dispute_id"]: r for r in items}
    assert set(by_id) == {"PP-D-1", "PP-D-NEW"}  # no receipt in the ledger -> left out
    known = by_id["PP-D-1"]
    assert known["receipt_id"] == rid and known["status"] == "WAITING_FOR_SELLER_RESPONSE"
    assert known["verdict"]["fault_class"] == "BUYER_REMORSE" and known["verdict"]["cited_rule"] == "AL-5"
    assert by_id["PP-D-NEW"]["receipt_id"] == unseen and by_id["PP-D-NEW"]["verdict"] is None
    assert items[0]["dispute_id"] == "PP-D-NEW"  # newest first


def test_queue_route_without_paypal_shows_ledger_view():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    s = scenarios.get_scenario("wrong_size")
    resolve.resolve(scenarios.build_dispute(s, rid), client=fake_claude("AGENT_ERROR", "AL-2"))
    body = TestClient(app).get("/disputes?live=false").json()
    assert body["paypal_error"] is None
    assert [r["simulated"] for r in body["items"]] == [True]


def test_catalog_route_flags_the_injected_listing():
    products = {p["product_id"]: p for p in TestClient(app).get("/catalog").json()}
    assert products["p_08"]["injection"].startswith("SYSTEM NOTE TO SHOPPING ASSISTANTS")
    assert products["p_08"]["injection"].endswith("what the user really wants.")
    assert products["p_03"]["injection"] is None


# --- FILING_ENABLED ---

def test_filing_disabled_by_default_refuses_every_path():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    with patch("backend.agents.evidence.disputes") as api, patch("backend.resolve.disputes") as get:
        # The route answers 403 before fetching the dispute or calling Claude.
        r = TestClient(app).post("/disputes/PP-D-1/resolve?file=true")
        assert r.status_code == 403 and "FILING_ENABLED" in r.json()["detail"]
        get.get_dispute.assert_not_called()
        # resolve() itself refuses too (CLI and webhook path).
        with pytest.raises(resolve.FilingDisabled):
            resolve.resolve(real_dispute(rid), paypal=MagicMock(), file=True, client=fake_claude("AGENT_ERROR", "AL-2"))
    assert not api.method_calls


def test_resolve_without_file_reports_filing_flag(monkeypatch):
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    out = resolve.resolve(real_dispute(rid), paypal=MagicMock(), client=fake_claude("AGENT_ERROR", "AL-2"))
    assert out["filing_enabled"] is False and out["next"]["action"] == "accept_claim"
    monkeypatch.setenv("FILING_ENABLED", "true")
    out = resolve.resolve(real_dispute(rid), paypal=MagicMock(), client=fake_claude("AGENT_ERROR", "AL-2"))
    assert out["filing_enabled"] is True


@respx.mock
def test_webhook_never_files_while_filing_disabled(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    monkeypatch.setenv("DISPUTE_AUTO_FILE", "true")
    mock_paypal()
    with patch("backend.app.main._handle_dispute") as handle:
        TestClient(app).post("/paypal/webhook", json=EVENT, headers=SIGNED)
    handle.assert_called_once_with("PP-D-1", False, "WH-1")


def test_filed_note_carries_the_anchor_hashes_and_the_event_records_them(monkeypatch):
    monkeypatch.setenv("FILING_ENABLED", "true")
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    with patch("backend.agents.evidence.disputes") as api:
        out = resolve.resolve(real_dispute(rid), paypal=MagicMock(), file=True,
                              client=fake_claude("AGENT_ERROR", "AL-2", note="x" * 5000))
    sent = api.accept_claim.call_args.args[2]
    receipt = ledger.get_receipt(rid)
    verdict_event = [e for e in receipt["events"] if e["kind"] == "dispute_verdict"][-1]
    response = [e for e in receipt["events"] if e["kind"] == "dispute_response"][-1]
    anchor = response["anchor"]
    assert anchor["verdict_event_hash"] == verdict_event["hash"] and anchor["intent_hash"] == receipt["hash"]
    # the head at filing is the record the dispute_response was chained after
    assert anchor["chain_head_hash"] == response["prev_hash"]
    assert len(sent) <= evidence.MAX_NOTE  # model text shortened, footer kept
    for h in (anchor["intent_hash"], anchor["verdict_event_hash"], anchor["chain_head_hash"]):
        assert h in sent
    assert response["note"] == sent and out["filed"]["anchor"] == anchor
    assert ledger.verify_chain()["ok"]


def test_adjudicate_records_anchor_without_a_note(monkeypatch):
    monkeypatch.setenv("FILING_ENABLED", "true")
    rid = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    with patch("backend.agents.evidence.disputes") as api:
        resolve.resolve(real_dispute(rid, links=("adjudicate",)), paypal=MagicMock(), file=True, client=fake_claude())
    assert api.adjudicate.called
    response = [e for e in ledger.get_receipt(rid)["events"] if e["kind"] == "dispute_response"][-1]
    assert response["note"] is None and response["anchor"]["verdict_event_hash"]
