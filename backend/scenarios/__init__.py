"""Seeded demo failures ("break the agent"). The agent's decision is fixed data, not an LLM
run, so each scenario produces the same receipt and the same facts every time.

A run logs the scenario's receipt, buys the chosen product for real through checkout (vaulted
sandbox order, anchored by invoice_id) when PAYPAL_VAULT_ID is set, then builds a PayPal-shaped
dispute for it. Only the dispute is simulated: it carries the real capture id, so the anchor
check runs, and it can never be filed.

tampered_receipt is the fifth scenario: wrong_size, bought for real, then the intent is
rewritten (the buyer "asked for size 9") and the chain rehashed. verify_chain still passes;
PayPal's invoice_id doesn't match, so the verdict goes to manual review.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic

from backend import checkout, ledger, resolve
from backend.catalog import get_product, listing_snapshot
from backend.config import get_settings
from backend.ledger.tamper import DEMO_MODEL, rewrite_intent
from backend.paypal.client import PayPalClient

SCENARIOS_PATH = Path(__file__).with_name("demo_scenarios.json")
SIMULATED_MODEL = DEMO_MODEL

TAMPER = {
    "name": "tampered_receipt",
    "title": "Tamper with a receipt",
    "breaks": "After the agent buys size 9, someone with database access rewrites the receipt to say "
              "the buyer asked for size 9, and rehashes the whole chain so it still verifies.",
    "expected_fault": None,
    "expected_status": "needs_review",
    "base": "wrong_size",
}


class ScenarioError(RuntimeError):
    pass


@lru_cache
def _load() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(SCENARIOS_PATH.read_text(encoding="utf-8")))


def list_scenarios() -> list[dict[str, Any]]:
    seeded = [{k: s[k] for k in ("name", "title", "breaks", "expected_fault")} | {"expected_status": "decided"}
              for s in _load()]
    return seeded + [{k: TAMPER[k] for k in ("name", "title", "breaks", "expected_fault", "expected_status")}]


def get_scenario(name: str) -> dict[str, Any] | None:
    if name == TAMPER["name"]:
        base = get_scenario(TAMPER["base"])
        return {**base, **{k: v for k, v in TAMPER.items() if k != "base"}, "tamper": True}
    found = next((s for s in _load() if s["name"] == name), None)
    return copy.deepcopy(found)


def log_receipt(scenario: dict[str, Any]) -> str:
    """Log the scenario's seeded intent as a new receipt."""
    run = scenario["agent_run"]
    snapshot = listing_snapshot(run["chosen_product_id"], run["chosen_size"])
    return ledger.log_intent(scenario["mandate"], {"model": SIMULATED_MODEL, **run, "chosen_listing": snapshot})


def build_dispute(scenario: dict[str, Any], receipt_id: str, capture_id: str | None = None) -> dict[str, Any]:
    """A dispute shaped like PayPal's GET /v1/customer/disputes/{id}. With capture_id (a real
    order), the anchor check runs on it; without, it's skipped."""
    run = scenario["agent_run"]
    product = get_product(run["chosen_product_id"])
    amount = {"currency_code": product["currency"], "value": f"{product['price']:.2f}"}
    item = f"{product['name']}, size {run['chosen_size']} ({product['product_id']})"
    return {
        "dispute_id": f"SIM-{scenario['name']}-{receipt_id[-6:]}",
        "reason": scenario["dispute"]["reason"],
        "status": "OPEN",
        "dispute_life_cycle_stage": "INQUIRY",
        "dispute_amount": amount,
        "disputed_transactions": [{"seller_transaction_id": capture_id, "custom": receipt_id, "gross_amount": amount,
                                   "items": [{"item_name": item, "reason": scenario["dispute"]["reason"]}]}],
        "messages": [{"posted_by": "BUYER", "content": scenario["dispute"]["buyer_message"]}],
        "links": [],
        "simulated": True,
    }


def _buyer_said_size_9(body: dict[str, Any]) -> None:
    """The tamper: make the mandate match what was bought, and drop the deviation note."""
    mandate = body["mandate"]
    mandate["constraints"]["size"] = "9"
    mandate["request_text"] = mandate["request_text"].replace("size 8", "size 9")
    for step in body["agent_run"].get("steps") or []:
        step.pop("deviation", None)


def run(name: str, paypal: PayPalClient | None = None,
        client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    """Run a scenario end to end. Returns the resolve result plus the order and receipt."""
    scenario = get_scenario(name)
    if scenario is None:
        raise KeyError(name)
    live = paypal is not None and bool(get_settings().paypal_vault_id)
    if scenario.get("tamper") and not live:
        raise ScenarioError("Tampering needs a real anchored order: set PayPal credentials and PAYPAL_VAULT_ID")

    receipt_id = log_receipt(scenario)
    order = checkout.start(paypal, receipt_id) if live else None
    capture_id = order.get("capture_id") if order else None
    if live and not capture_id:
        raise ScenarioError(f"Vaulted order {order['order_id']} was not captured ({order['status']})")
    tampered = rewrite_intent(receipt_id, _buyer_said_size_9) if scenario.get("tamper") else None

    result = resolve.resolve(build_dispute(scenario, receipt_id, capture_id),
                             paypal=paypal if capture_id else None, client=client)
    return {"scenario": name, "expected_fault": scenario["expected_fault"],
            "expected_status": scenario.get("expected_status", "decided"), **result,
            "order": order, "tampered": tampered, "receipt": ledger.get_receipt(receipt_id)}
