"""The buy flow end to end: agent decides -> intent logged -> PayPal order created."""
from collections.abc import Callable
from typing import Any

import anthropic

from backend import checkout, ledger
from backend.agents import shopping
from backend.paypal.client import PayPalClient


def buy(request_text: str, paypal: PayPalClient, client: anthropic.Anthropic | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """progress (optional) gets {"kind": "step" | "logged" | "ordering" | "ordered", ...} as the run goes."""
    emit = progress or (lambda event: None)
    decision = shopping.run(request_text, client=client, on_step=lambda step: emit({"kind": "step", "step": step}))
    receipt_id = ledger.log_intent(decision["mandate"], decision["agent_run"])
    emit({"kind": "logged", "receipt_id": receipt_id, "constraints": decision["mandate"]["constraints"],
          "chosen_product_id": decision["agent_run"]["chosen_product_id"]})
    result: dict[str, Any] = {"receipt_id": receipt_id, **decision}
    if decision["agent_run"]["chosen_product_id"]:
        emit({"kind": "ordering"})
        result["checkout"] = checkout.start(paypal, receipt_id)
        emit({"kind": "ordered", "checkout": result["checkout"]})
    return result
