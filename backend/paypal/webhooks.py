"""PayPal webhooks: verify the signature, then pick out dispute events.

Verification uses PayPal's verify-webhook-signature API with the headers PayPal sent and
our webhook id (PAYPAL_WEBHOOK_ID, from the developer dashboard when the webhook is created).
"""
from collections.abc import Mapping
from typing import Any

from backend.paypal.client import PayPalClient

DISPUTE_EVENTS = {"CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"}
_SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def verify_signature(client: PayPalClient, headers: Mapping[str, str], event: dict[str, Any], webhook_id: str) -> bool:
    """True only if PayPal confirms the event was signed for our webhook. headers is matched
    case-insensitively when it's a Starlette Headers object; pass lower-case keys otherwise."""
    fields = {name: headers.get(header) for name, header in _SIGNATURE_HEADERS.items()}
    if not all(fields.values()):
        return False
    body = {**fields, "webhook_id": webhook_id, "webhook_event": event}
    result = client.request("POST", "/v1/notifications/verify-webhook-signature", json=body)
    return isinstance(result, dict) and result.get("verification_status") == "SUCCESS"


def dispute_id_of(event: dict[str, Any]) -> str | None:
    """The dispute a CUSTOMER.DISPUTE.* event is about, or None for other events."""
    if event.get("event_type") not in DISPUTE_EVENTS:
        return None
    return (event.get("resource") or {}).get("dispute_id")
