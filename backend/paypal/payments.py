"""PayPal Payments v2: captures.

A dispute names the capture it's about (disputed_transactions[].seller_transaction_id).
The capture carries the order's custom_id (our receipt_id) and invoice_id (the intent hash),
so one GET answers both "which receipt" and "does it still match".
"""
from typing import Any

from backend.paypal.client import PayPalClient


def get_capture(client: PayPalClient, capture_id: str) -> dict[str, Any]:
    return client.request("GET", f"/v2/payments/captures/{capture_id}")


def order_id_of(capture: dict[str, Any]) -> str | None:
    """The order a capture belongs to, from its rel "up" link."""
    for link in capture.get("links", []):
        if link.get("rel") == "up" and "/checkout/orders/" in link.get("href", ""):
            return link["href"].rstrip("/").rsplit("/", 1)[-1]
    return None
