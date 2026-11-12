"""PayPal Orders v2: create / get / capture."""
from typing import Any

from backend.paypal.client import PayPalClient


def _request_id(request_id: str | None) -> dict[str, str] | None:
    """A stable PayPal-Request-Id, so a retry of the same call is idempotent under PayPal's idempotency
    handling. None keeps the client's random per-call id."""
    return {"PayPal-Request-Id": request_id} if request_id else None


def create_order(
    client: PayPalClient,
    amount: str,
    currency: str = "USD",
    description: str | None = None,
    custom_id: str | None = None,
    invoice_id: str | None = None,
    return_url: str = "https://example.com/return",
    cancel_url: str = "https://example.com/cancel",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Create a CAPTURE-intent order. The buyer must approve it before capture."""
    unit: dict[str, Any] = {"amount": {"currency_code": currency, "value": amount}}
    if description:
        unit["description"] = description
    if custom_id:
        # Links the PayPal order back to our ledger receipt.
        unit["custom_id"] = custom_id
    if invoice_id:
        # The intent's hash, held by PayPal, so a rewritten ledger can't match it (see ledger/anchor.py).
        unit["invoice_id"] = invoice_id
    body = {
        "intent": "CAPTURE",
        "purchase_units": [unit],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }
    return client.request("POST", "/v2/checkout/orders", json=body, headers=_request_id(request_id))


def create_vaulted_order(
    client: PayPalClient,
    amount: str,
    vault_id: str,
    currency: str = "USD",
    description: str | None = None,
    custom_id: str | None = None,
    invoice_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Charge a saved PayPal wallet. In sandbox this returns COMPLETED, already captured:
    no buyer redirect and no separate capture call."""
    unit: dict[str, Any] = {"amount": {"currency_code": currency, "value": amount}}
    if description:
        unit["description"] = description
    if custom_id:
        unit["custom_id"] = custom_id
    if invoice_id:
        unit["invoice_id"] = invoice_id
    body = {"intent": "CAPTURE", "purchase_units": [unit], "payment_source": {"paypal": {"vault_id": vault_id}}}
    return client.request("POST", "/v2/checkout/orders", json=body, headers=_request_id(request_id))


def get_order(client: PayPalClient, order_id: str) -> dict[str, Any]:
    return client.request("GET", f"/v2/checkout/orders/{order_id}")


def capture_order(client: PayPalClient, order_id: str) -> dict[str, Any]:
    return client.request("POST", f"/v2/checkout/orders/{order_id}/capture", json={},
                          headers=_request_id(f"capture-{order_id}"))


def approve_link(order: dict[str, Any]) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    return None


def capture_id(captured: dict[str, Any]) -> str | None:
    try:
        return captured["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError):
        return None
