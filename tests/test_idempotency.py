"""Filing, webhook delivery and resolve() are safe to repeat."""
import threading
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend import ledger, resolve, scenarios
from backend.app import main
from backend.app.main import app
from backend.paypal import disputes
from backend.paypal.client import PayPalClient
from tests.test_disputes_flow import BASE, EVENT, SIGNED, fake_claude, mock_paypal, real_dispute


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("FILING_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", BASE)
    monkeypatch.setattr(resolve, "_anchor", lambda *args, **kwargs: None)


# --- PayPal-Request-Id ---

def test_request_id_is_deterministic_per_dispute_and_action():
    assert disputes.request_id("PP-D-1", "accept_claim") == disputes.request_id("PP-D-1", "accept_claim")
    assert disputes.request_id("PP-D-1", "accept_claim") != disputes.request_id("PP-D-1", "escalate")
    assert disputes.request_id("PP-D-1", "accept_claim") != disputes.request_id("PP-D-2", "accept_claim")
    assert len(disputes.request_id("PP-D-1", "x")) <= 108  # PayPal's limit


@respx.mock
@pytest.mark.parametrize("action, call, path", [
    ("accept_claim", lambda c: disputes.accept_claim(c, "PP-D-1", "n"), "accept-claim"),
    ("escalate", lambda c: disputes.escalate(c, "PP-D-1", "n"), "escalate"),
    ("provide_evidence", lambda c: disputes.provide_evidence(c, "PP-D-1", "n"), "provide-evidence"),
    ("adjudicate", lambda c: disputes.adjudicate(c, "PP-D-1", disputes.SELLER_FAVOUR), "adjudicate"),
])
def test_filing_calls_send_the_deterministic_request_id(action, call, path):
    mock_paypal()
    route = respx.post(f"{BASE}/v1/customer/disputes/PP-D-1/{path}").mock(return_value=httpx.Response(200, json={}))
    client = PayPalClient()
    call(client)
    call(client)  # a retry
    sent = {r.request.headers["paypal-request-id"] for r in route.calls}
    assert sent == {disputes.request_id("PP-D-1", action)}


# --- webhook dedupe ---

def post_event(event=EVENT):
    return TestClient(app).post("/paypal/webhook", json=event, headers=SIGNED)


@respx.mock
def test_repeated_webhook_event_is_handled_once(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    mock_paypal()
    with patch("backend.app.main._handle_dispute") as handle:
        first, second = post_event(), post_event()
    assert first.json().get("duplicate") is None and second.json()["duplicate"] is True
    assert second.status_code == 200
    handle.assert_called_once()


@respx.mock
def test_a_different_event_id_is_handled(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    mock_paypal()
    with patch("backend.app.main._handle_dispute") as handle:
        post_event()
        post_event({**EVENT, "id": "WH-2"})
    assert handle.call_count == 2


@respx.mock
def test_unverified_event_does_not_use_up_its_id(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WHID")
    mock_paypal("FAILURE")
    assert post_event().status_code == 400
    assert ledger.claim_event("WH-1") is True  # still unclaimed


def test_failed_handling_releases_the_event_for_redelivery():
    assert ledger.claim_event("WH-9") is True
    assert ledger.claim_event("WH-9") is False
    with patch("backend.app.main.resolve.resolve_by_id", side_effect=RuntimeError("boom")), \
            patch("backend.app.main.PayPalClient"):
        main._handle_dispute("PP-D-1", False, "WH-9")
    assert ledger.claim_event("WH-9") is True


def test_ledger_chain_is_unaffected_by_event_bookkeeping():
    ledger.claim_event("WH-1")
    assert ledger.verify_chain()["ok"] is True


# --- per-dispute lock ---

def test_concurrent_resolves_of_one_dispute_decide_once():
    rid = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    client = fake_claude()
    barrier = threading.Barrier(4)
    results = []

    def work():
        barrier.wait()
        results.append(resolve.resolve(real_dispute(rid), client=client))

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 4
    assert client.messages.create.call_count == 1  # one adjudication
    kinds = [e["kind"] for e in ledger.get_receipt(rid)["events"]]
    assert kinds.count("dispute_verdict") == 1 and kinds.count("dispute_opened") == 1


def test_locks_are_per_dispute():
    assert resolve._dispute_lock("PP-D-1") is resolve._dispute_lock("PP-D-1")
    assert resolve._dispute_lock("PP-D-1") is not resolve._dispute_lock("PP-D-2")
