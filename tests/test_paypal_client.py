import json

import httpx
import pytest
import respx

from backend.paypal import disputes, orders, vault
from backend.paypal.client import PayPalClient, PayPalError

BASE = "https://api-m.test.paypal.com"


@pytest.fixture
def client():
    return PayPalClient(client_id="id", client_secret="secret", base_url=BASE)


def mock_token(mock, expires_in=3600):
    return mock.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": expires_in})
    )


@respx.mock
def test_token_is_cached(client):
    token_route = mock_token(respx)
    respx.get(f"{BASE}/v2/checkout/orders/O1").mock(return_value=httpx.Response(200, json={"id": "O1"}))
    orders.get_order(client, "O1")
    orders.get_order(client, "O1")
    assert token_route.call_count == 1


@respx.mock
def test_expired_token_is_refreshed(client):
    token_route = mock_token(respx, expires_in=0)
    respx.get(f"{BASE}/v2/checkout/orders/O1").mock(return_value=httpx.Response(200, json={"id": "O1"}))
    orders.get_order(client, "O1")
    orders.get_order(client, "O1")
    assert token_route.call_count == 2


@respx.mock
def test_create_order_sends_bearer_and_request_id(client):
    mock_token(respx)
    route = respx.post(f"{BASE}/v2/checkout/orders").mock(
        return_value=httpx.Response(
            201,
            json={"id": "O1", "links": [{"rel": "payer-action", "href": "https://approve"}]},
        )
    )
    order = orders.create_order(client, "109.99", custom_id="rcpt_1", invoice_id="a" * 64)
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["PayPal-Request-Id"]
    body = json.loads(req.content)
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "109.99"}
    assert body["purchase_units"][0]["custom_id"] == "rcpt_1"
    assert body["purchase_units"][0]["invoice_id"] == "a" * 64
    assert orders.approve_link(order) == "https://approve"


@respx.mock
def test_error_carries_status_and_debug_id(client):
    mock_token(respx)
    respx.post(f"{BASE}/v2/checkout/orders/O1/capture").mock(
        return_value=httpx.Response(
            422, json={"name": "UNPROCESSABLE_ENTITY"}, headers={"paypal-debug-id": "dbg123"}
        )
    )
    with pytest.raises(PayPalError) as exc:
        orders.capture_order(client, "O1")
    assert exc.value.status == 422
    assert exc.value.debug_id == "dbg123"
    assert exc.value.body["name"] == "UNPROCESSABLE_ENTITY"


@respx.mock
def test_provide_evidence_is_multipart(client):
    mock_token(respx)
    route = respx.post(f"{BASE}/v1/customer/disputes/D1/provide-evidence").mock(
        return_value=httpx.Response(200, json={"links": []})
    )
    disputes.provide_evidence(client, "D1", "matches mandate")
    req = route.calls.last.request
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    assert b'"evidence_type": "OTHER"' in req.content


def test_capture_id_extraction():
    captured = {"purchase_units": [{"payments": {"captures": [{"id": "C1"}]}}]}
    assert orders.capture_id(captured) == "C1"
    assert orders.capture_id({}) is None


def test_missing_credentials_raise(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError):
        PayPalClient(base_url=BASE)


@respx.mock
def test_vaulted_order_sends_vault_id_only(client):
    mock_token(respx)
    route = respx.post(f"{BASE}/v2/checkout/orders").mock(
        return_value=httpx.Response(201, json={"id": "O2", "status": "COMPLETED"})
    )
    orders.create_vaulted_order(client, "109.99", "VAULT1", custom_id="rcpt_1", invoice_id="b" * 64)
    body = json.loads(route.calls.last.request.content)
    assert body["payment_source"] == {"paypal": {"vault_id": "VAULT1"}}
    assert body["purchase_units"][0]["custom_id"] == "rcpt_1"
    assert body["purchase_units"][0]["invoice_id"] == "b" * 64


@respx.mock
def test_setup_token_then_payment_token(client):
    mock_token(respx)
    respx.post(f"{BASE}/v3/vault/setup-tokens").mock(return_value=httpx.Response(
        201, json={"id": "ST1", "status": "PAYER_ACTION_REQUIRED",
                   "links": [{"rel": "approve", "href": "https://approve/ST1"}]}))
    pt_route = respx.post(f"{BASE}/v3/vault/payment-tokens").mock(
        return_value=httpx.Response(201, json={"id": "VAULT1"}))

    token = vault.create_setup_token(client)
    assert vault.approve_link(token) == "https://approve/ST1"
    assert vault.create_payment_token(client, "ST1")["id"] == "VAULT1"
    assert json.loads(pt_route.calls.last.request.content) == {
        "payment_source": {"token": {"id": "ST1", "type": "SETUP_TOKEN"}}
    }
