"""Real disputes fail closed when the PayPal anchor can't be checked."""
import pytest
import respx
import httpx

from backend import ledger, resolve, scenarios
from backend.agents import adjudicator
from backend.paypal.client import PayPalClient
from tests.test_disputes_flow import BASE, fake_claude, real_dispute

CAPTURE = "CAP123"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("FILING_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", BASE)


def _receipt():
    return scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))


def _token(mock):
    mock.post("/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))


def test_real_dispute_without_capture_id_needs_review_and_skips_the_model():
    rid = _receipt()
    client = fake_claude()
    out = resolve.resolve(real_dispute(rid, capture_id=None), client=client)
    client.messages.create.assert_not_called()
    verdict = out["verdict"]
    assert verdict["status"] == "needs_review"
    assert verdict["integrity"]["status"] == "failed" and verdict["integrity"]["check"] == "anchor_unavailable"


def test_failed_capture_lookup_needs_review():
    rid = _receipt()
    client = fake_claude()
    with respx.mock(base_url=BASE) as mock:
        _token(mock)
        mock.get(f"/v2/payments/captures/{CAPTURE}").mock(return_value=httpx.Response(500, json={"name": "X"}))
        out = resolve.resolve(real_dispute(rid, capture_id=CAPTURE), paypal=PayPalClient(), client=client)
    client.messages.create.assert_not_called()
    assert out["verdict"]["integrity"]["check"] == "anchor_unavailable"
    assert out["verdict"]["status"] == "needs_review"


def test_no_paypal_client_for_a_real_dispute_fails_closed():
    rid = _receipt()
    out = resolve.resolve(real_dispute(rid, capture_id=CAPTURE), paypal=None, client=fake_claude())
    assert out["verdict"]["integrity"]["check"] == "anchor_unavailable"


def test_order_without_invoice_id_is_still_unanchored_not_failed():
    rid = _receipt()
    client = fake_claude()
    with respx.mock(base_url=BASE) as mock:
        _token(mock)
        mock.get(f"/v2/payments/captures/{CAPTURE}").mock(return_value=httpx.Response(200, json={
            "id": CAPTURE, "custom_id": rid,
            "links": [{"rel": "up", "href": f"{BASE}/v2/checkout/orders/ORD1"}]}))
        out = resolve.resolve(real_dispute(rid, capture_id=CAPTURE), paypal=PayPalClient(), client=client)
    assert out["verdict"]["integrity"]["status"] == "unanchored"
    assert out["verdict"]["status"] == "decided"


def test_simulated_dispute_without_real_order_stays_unanchored():
    s = scenarios.get_scenario("buyer_remorse")
    rid = scenarios.log_receipt(s)
    out = resolve.resolve(scenarios.build_dispute(s, rid), client=fake_claude())
    assert out["verdict"]["integrity"]["status"] == "unanchored"


def test_unavailable_verdict_is_rechecked_on_the_next_run():
    rid = _receipt()
    first = resolve.resolve(real_dispute(rid, capture_id=None), client=fake_claude())
    assert first["verdict"]["status"] == "needs_review"
    with respx.mock(base_url=BASE) as mock:
        _token(mock)
        mock.get(f"/v2/payments/captures/{CAPTURE}").mock(return_value=httpx.Response(200, json={
            "id": CAPTURE, "custom_id": rid, "invoice_id": ledger.chain.intent_hash(rid),
            "links": [{"rel": "up", "href": f"{BASE}/v2/checkout/orders/ORD1"}]}))
        second = resolve.resolve(real_dispute(rid, capture_id=CAPTURE), paypal=PayPalClient(),
                                 client=fake_claude())
    assert second["verdict"]["integrity"]["status"] == "verified"
    assert second["verdict"]["status"] == "decided"


def test_integrity_status_marks_unavailable():
    chain = {"ok": True, "records": 1, "head_hash": "x", "bad_seq": None, "error": None}
    got = adjudicator.integrity_status(chain, {"unavailable": "boom"})
    assert got["status"] == "failed" and got["check"] == "anchor_unavailable"
