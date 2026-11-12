"""The buy flow end to end: agent decides -> intent logged -> PayPal order created."""
from collections.abc import Callable
from typing import Any

import anthropic

from backend import checkout, ledger
from backend.agents import shopping
from backend.paypal.client import PayPalClient


def buy(request_text: str, paypal: PayPalClient, client: anthropic.Anthropic | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
        constraints: dict[str, Any] | None = None, confirmed_at: str | None = None,
        confirmation: str = "cli", guard: bool = False) -> dict[str, Any]:
    """progress (optional) gets {"kind": "step" | "logged" | "ordering" | "ordered", ...} as the run goes.

    constraints / confirmed_at: what the buyer confirmed through /mandate/{id}/confirm, and when (server
    time). They are recorded in the receipt's mandate exactly as given, with confirmation "buyer".
    Without them the agent reads the request itself and the mandate records `confirmation`
    ("cli" for scripts.shop).

    guard=True (the public /shop): the choice is checked against the confirmed constraints before any
    PayPal call; a violating choice is blocked and result["checkout"]["blocked"] is True."""
    emit = progress or (lambda event: None)
    if constraints is not None:
        confirmation = "buyer"
    decision = shopping.run(request_text, client=client, on_step=lambda step: emit({"kind": "step", "step": step}),
                            constraints=constraints, confirmed_at=confirmed_at)
    decision["mandate"]["confirmation"] = confirmation
    receipt_id = ledger.log_intent(decision["mandate"], decision["agent_run"])
    emit({"kind": "logged", "receipt_id": receipt_id, "constraints": decision["mandate"]["constraints"],
          "chosen_product_id": decision["agent_run"]["chosen_product_id"]})
    result: dict[str, Any] = {"receipt_id": receipt_id, **decision}
    if decision["agent_run"]["chosen_product_id"]:
        emit({"kind": "ordering"})
        result["checkout"] = checkout.start(paypal, receipt_id, guard=guard)
        emit({"kind": "blocked" if result["checkout"].get("blocked") else "ordered", "checkout": result["checkout"]})
    return result
