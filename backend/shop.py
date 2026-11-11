"""The buy flow end to end: agent decides -> intent logged -> PayPal order created."""
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import anthropic

from backend import checkout, ledger
from backend.agents import shopping
from backend.paypal.client import PayPalClient


def buy(request_text: str, paypal: PayPalClient, client: anthropic.Anthropic | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
        constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    """progress (optional) gets {"kind": "step" | "logged" | "ordering" | "ordered", ...} as the run goes.

    constraints: what the buyer confirmed on the task page. They are stamped with the time of
    confirmation and recorded in the receipt's mandate exactly as given."""
    emit = progress or (lambda event: None)
    confirmed_at = datetime.now(timezone.utc).isoformat() if constraints is not None else None
    decision = shopping.run(request_text, client=client, on_step=lambda step: emit({"kind": "step", "step": step}),
                            constraints=constraints, confirmed_at=confirmed_at)
    receipt_id = ledger.log_intent(decision["mandate"], decision["agent_run"])
    emit({"kind": "logged", "receipt_id": receipt_id, "constraints": decision["mandate"]["constraints"],
          "chosen_product_id": decision["agent_run"]["chosen_product_id"]})
    result: dict[str, Any] = {"receipt_id": receipt_id, **decision}
    if decision["agent_run"]["chosen_product_id"]:
        emit({"kind": "ordering"})
        result["checkout"] = checkout.start(paypal, receipt_id)
        emit({"kind": "ordered", "checkout": result["checkout"]})
    return result
