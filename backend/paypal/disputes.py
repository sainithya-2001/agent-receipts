"""PayPal Disputes v1. adjudicate() is sandbox-only and needs status UNDER_REVIEW."""
import json
from typing import Any

from backend.paypal.client import PayPalClient

BASE = "/v1/customer/disputes"

BUYER_FAVOUR = "BUYER_FAVOR"
SELLER_FAVOUR = "SELLER_FAVOR"


def list_disputes(client: PayPalClient, **params: Any) -> dict[str, Any]:
    return client.request("GET", BASE, params=params or None)


def get_dispute(client: PayPalClient, dispute_id: str) -> dict[str, Any]:
    return client.request("GET", f"{BASE}/{dispute_id}")


def accept_claim(client: PayPalClient, dispute_id: str, note: str) -> dict[str, Any]:
    return client.request("POST", f"{BASE}/{dispute_id}/accept-claim", json={"note": note})


def escalate(client: PayPalClient, dispute_id: str, note: str) -> dict[str, Any]:
    """Escalate an inquiry to a claim, which moves it toward UNDER_REVIEW."""
    return client.request("POST", f"{BASE}/{dispute_id}/escalate", json={"note": note})


def provide_evidence(
    client: PayPalClient, dispute_id: str, notes: str, evidence_type: str = "OTHER"
) -> dict[str, Any]:
    """Submit text evidence. This endpoint only accepts multipart/form-data."""
    payload = {"evidences": [{"evidence_type": evidence_type, "notes": notes}]}
    files = {"input": (None, json.dumps(payload), "application/json")}
    return client.request("POST", f"{BASE}/{dispute_id}/provide-evidence", files=files)


def adjudicate(client: PayPalClient, dispute_id: str, outcome: str) -> dict[str, Any]:
    """Sandbox only. outcome is BUYER_FAVOUR or SELLER_FAVOUR."""
    return client.request(
        "POST", f"{BASE}/{dispute_id}/adjudicate", json={"adjudication_outcome": outcome}
    )
