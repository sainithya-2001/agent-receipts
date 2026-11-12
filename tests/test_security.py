"""Public-surface checks: ids can't steer calls at other PayPal endpoints, errors don't leak, and the
set of public routes is pinned so a new one has to be added here deliberately."""
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend import ledger
from backend.app.main import app
from backend.paypal.client import PayPalClient, UnsafePayPalPath

BASE = "https://api-m.sandbox.paypal.com"

# "../", query/fragment characters, encoded slashes and dots: each could reach a different endpoint
# (for example POST .../captures/ID/refund) with the app's own credentials.
EVIL = [
    "x/../../../payments/captures/CAP/refund?",
    "..%2f..%2fv1",
    "ABC#frag",
    "A B",
    "../x",
    "..",
    "a/b",
    "a%2fb",
    "a\\b",
    "A" * 65,
    "",
]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", BASE)
    monkeypatch.delenv("FILING_ENABLED", raising=False)


@pytest.mark.parametrize("token", EVIL)
def test_evil_ids_never_reach_paypal(token):
    client = TestClient(app)
    with respx.mock(assert_all_called=False) as paypal:
        any_call = paypal.route().mock(return_value=httpx.Response(200, json={}))
        calls = [
            client.get("/paypal/return", params={"token": token}),
            client.get("/paypal/cancel", params={"token": token}),
            client.get("/receipts/rcpt_x/paypal-anchor", params={"order_id": token}),
            client.get("/receipts/rcpt_x/paypal-anchor", params={"capture_id": token}),
            client.post(f"/disputes/{token}/resolve"),
        ]
        assert not any_call.called, "a bad id produced a PayPal request"
    for r in calls:
        assert r.status_code in (404, 405, 422), (token, r.status_code)


@pytest.mark.parametrize("path", [
    "/v2/checkout/orders/x/../../payments/captures/C/refund?/capture",
    "/v2/checkout/orders/..%2f/capture", "/v1/x#y", "/v1/x?y=1", "v1/x", "/v1//x", "/", "", "/v1/x/",
])
def test_the_client_itself_refuses_unsafe_paths(path):
    with respx.mock(assert_all_called=False) as paypal:
        route = paypal.route().mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(UnsafePayPalPath):
            PayPalClient().request("POST", path)
        assert not route.called


def test_normal_paypal_ids_still_pass():
    for ok in ["PP-R-LQT-10195344", "5WU40766WW401411R", "SIM-wrong_size-a74dfe", "rcpt_627e333bfb7045c1"]:
        assert TestClient(app).get("/paypal/cancel", params={"token": ok}).status_code == 200


def test_cancel_cannot_rewrite_a_captured_order():
    rid = ledger.log_intent(
        {"request_text": "x", "constraints": {"category": "shoes", "size": "8", "max_price": 120, "currency": "USD"}},
        {"model": "m", "options_considered": [], "chosen_product_id": "p_03", "reasoning": "r"})
    ledger.append_event(rid, "paypal_order_created", {"order_id": "ORD1", "status": "COMPLETED"})
    ledger.append_event(rid, "paypal_captured", {"order_id": "ORD1", "status": "COMPLETED", "capture_id": "C1"})
    before = len(ledger.get_receipt(rid)["events"])
    r = TestClient(app).get("/paypal/cancel", params={"token": "ORD1"})
    assert r.status_code == 200 and r.json()["receipt_id"] == rid
    assert len(ledger.get_receipt(rid)["events"]) == before  # nothing was added to a paid order

    rid2 = ledger.log_intent(
        {"request_text": "y", "constraints": {"category": "shoes", "size": "8", "max_price": 120, "currency": "USD"}},
        {"model": "m", "options_considered": [], "chosen_product_id": "p_03", "reasoning": "r"})
    ledger.append_event(rid2, "paypal_order_created", {"order_id": "ORD2", "status": "CREATED"})
    for _ in range(3):
        TestClient(app).get("/paypal/cancel", params={"token": "ORD2"})
    cancelled = [e for e in ledger.get_receipt(rid2)["events"] if e["kind"] == "paypal_cancelled"]
    assert len(cancelled) == 1  # a real cancel is recorded once


def test_server_errors_do_not_leak_details_or_secrets():
    client = TestClient(app, raise_server_exceptions=False)
    with patch("backend.app.main.ledger.verify_chain", side_effect=RuntimeError("PAYPAL_CLIENT_SECRET=hunter2 at C:/x.py")):
        r = client.get("/ledger/verify")
    assert r.status_code == 500
    assert "hunter2" not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text
    assert r.text.strip() == "Internal Server Error"


def test_no_cors_headers_so_other_origins_cannot_read_responses():
    r = TestClient(app).get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    pre = TestClient(app).options("/shop", headers={"Origin": "https://evil.example",
                                                    "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in {k.lower() for k in pre.headers}


def test_validation_errors_do_not_echo_secrets():
    r = TestClient(app).post("/shop", json={"mandate_id": "x", "request_text": "y"})
    assert r.status_code == 422
    assert "secret" not in r.text.lower() and "PAYPAL" not in r.text


# The public API, pinned. Adding or removing a route should fail this test until it is reviewed:
# what it can change, and whether the hosted demo should expose it.
EXPECTED_ROUTES = {
    ("GET", "/health"),
    ("POST", "/mandate/parse"), ("POST", "/mandate/{mandate_id}/confirm"),
    ("POST", "/shop"), ("GET", "/shop/jobs/{job_id}"),
    ("GET", "/paypal/return"), ("GET", "/paypal/cancel"), ("POST", "/paypal/webhook"),
    ("GET", "/receipts/{receipt_id}"), ("GET", "/receipts/{receipt_id}/paypal-anchor"),
    ("GET", "/ledger/verify"),
    ("GET", "/disputes"), ("POST", "/disputes/{dispute_id}/resolve"),
    ("GET", "/catalog"), ("GET", "/scenarios"), ("POST", "/scenarios/{name}/run"),
}
FRAMEWORK = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_the_public_route_list_is_pinned():
    found = {(m, r.path) for r in app.routes if hasattr(r, "methods") and r.path not in FRAMEWORK
             for m in r.methods if m not in ("HEAD", "OPTIONS")}
    assert found == EXPECTED_ROUTES


def test_no_route_can_rejudge_edit_or_delete():
    for r in app.routes:
        path = getattr(r, "path", "")
        assert "rejudge" not in path and "supersede" not in path
        assert not ({"PUT", "PATCH", "DELETE"} & set(getattr(r, "methods", set()) or set()))


def test_filing_is_refused_without_the_switch(monkeypatch):
    with respx.mock(assert_all_called=False) as paypal:
        route = paypal.route().mock(return_value=httpx.Response(200, json={}))
        r = TestClient(app).post("/disputes/PP-R-LQT-10195344/resolve", params={"file": "true"})
        assert not route.called  # refused before any PayPal call
    assert r.status_code == 403
