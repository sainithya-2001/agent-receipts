"""PayPal Vault v3: save the buyer's PayPal wallet once, so later orders need no redirect.

Flow: create_setup_token -> buyer approves the link (no charge) -> create_payment_token
-> its id is the vault_id used by orders.create_vaulted_order.
"""
from typing import Any

from backend.paypal.client import PayPalClient


def create_setup_token(
    client: PayPalClient,
    return_url: str = "https://example.com/return",
    cancel_url: str = "https://example.com/cancel",
) -> dict[str, Any]:
    body = {
        "payment_source": {
            "paypal": {
                "usage_type": "MERCHANT",
                "customer_type": "CONSUMER",
                "experience_context": {"return_url": return_url, "cancel_url": cancel_url},
            }
        }
    }
    return client.request("POST", "/v3/vault/setup-tokens", json=body)


def get_setup_token(client: PayPalClient, setup_token_id: str) -> dict[str, Any]:
    return client.request("GET", f"/v3/vault/setup-tokens/{setup_token_id}")


def create_payment_token(client: PayPalClient, setup_token_id: str) -> dict[str, Any]:
    """Exchange an approved setup token for a reusable payment token (the vault_id)."""
    body = {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}}
    return client.request("POST", "/v3/vault/payment-tokens", json=body)


def approve_link(setup_token: dict[str, Any]) -> str | None:
    return next((l["href"] for l in setup_token.get("links", []) if l.get("rel") == "approve"), None)
