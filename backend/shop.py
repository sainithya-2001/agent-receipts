"""The buy flow end to end: agent decides -> intent logged -> PayPal order created."""
from typing import Any

import anthropic

from backend import checkout, ledger
from backend.agents import shopping
from backend.paypal.client import PayPalClient


def buy(request_text: str, paypal: PayPalClient, client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    decision = shopping.run(request_text, client=client)
    receipt_id = ledger.log_intent(decision["mandate"], decision["agent_run"])
    result: dict[str, Any] = {"receipt_id": receipt_id, **decision}
    if decision["agent_run"]["chosen_product_id"]:
        result["checkout"] = checkout.start(paypal, receipt_id)
    return result
