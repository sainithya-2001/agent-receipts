"""GET /receipts/{id}/paypal-anchor feeds the receipt page's anchor panel."""
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend import ledger, scenarios
from backend.app.main import app
from backend.ledger import chain

BASE = "https://api-m.sandbox.paypal.com"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", BASE)


def capture(rid, invoice_id):
    return {"id": "CAP1", "custom_id": rid, "invoice_id": invoice_id,
            "links": [{"rel": "up", "href": f"{BASE}/v2/checkout/orders/ORD1"}]}


@respx.mock
def test_capture_id_param_returns_both_hashes_and_custom_id():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    respx.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE}/v2/payments/captures/CAP1").mock(
        return_value=httpx.Response(200, json=capture(rid, chain.intent_hash(rid))))
    body = TestClient(app).get(f"/receipts/{rid}/paypal-anchor", params={"capture_id": "CAP1"}).json()
    assert body["ok"] is True and body["intent_hash"] == body["invoice_id"]
    assert body["custom_id"] == rid and body["capture_id"] == "CAP1" and body["order_id"] == "ORD1"


@respx.mock
def test_mismatch_reports_different_hashes():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    respx.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE}/v2/payments/captures/CAP1").mock(
        return_value=httpx.Response(200, json=capture(rid, "0" * 64)))
    body = TestClient(app).get(f"/receipts/{rid}/paypal-anchor", params={"capture_id": "CAP1"}).json()
    assert body["ok"] is False and body["anchored"] is True
    assert body["intent_hash"] != body["invoice_id"]


def test_without_credentials_the_panel_gets_a_503_not_a_crash(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "")
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    assert TestClient(app).get(f"/receipts/{rid}/paypal-anchor").status_code == 503


@respx.mock
def test_tampered_receipt_chain_verifies_but_panel_data_shows_the_edit():
    """What the mismatch panel says: the chain still verifies; PayPal's copy differs; custom_id is the receipt's."""
    from backend.ledger.tamper import rewrite_intent

    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    original = chain.intent_hash(rid)
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1", "invoice_id": original})
    rewrite_intent(rid, lambda body: body["mandate"]["constraints"].update(size="9"))
    respx.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE}/v2/payments/captures/CAP1").mock(
        return_value=httpx.Response(200, json=capture(rid, original)))  # PayPal still holds the original hash
    client = TestClient(app)
    body = client.get(f"/receipts/{rid}/paypal-anchor", params={"capture_id": "CAP1"}).json()
    assert ledger.verify_chain()["ok"] is True
    assert client.get("/ledger/verify").json()["ok"] is True
    assert body["ok"] is False and body["intent_hash"] != body["invoice_id"] == original
    assert body["custom_id"] == rid and body["custom_id_ok"] is True


@respx.mock
def test_custom_id_of_another_receipt_is_reported_as_is():
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    respx.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE}/v2/payments/captures/CAP1").mock(
        return_value=httpx.Response(200, json=capture("rcpt_0000000000000000", "0" * 64)))
    body = TestClient(app).get(f"/receipts/{rid}/paypal-anchor", params={"capture_id": "CAP1"}).json()
    assert body["custom_id"] == "rcpt_0000000000000000" and body["custom_id_ok"] is False
