"""Turn a logged intent into a PayPal order, and capture it after the buyer approves.

Rule: no PayPal order without a receipt_id. The intent is logged first; the order carries
the receipt_id as custom_id and the intent's hash as invoice_id (the anchor that
ledger.verify_receipt_against_paypal checks), and PayPal IDs are appended to the receipt as events.

With PAYPAL_VAULT_ID set, the order charges the buyer's saved wallet and completes at once.
Without it, the buyer approves via the redirect link and /paypal/return captures.
"""
from typing import Any

from backend import ledger
from backend.catalog import get_product
from backend import guard as purchase_guard
from backend.config import get_settings
from backend.paypal import orders
from backend.paypal.client import PayPalClient


class CheckoutError(RuntimeError):
    pass


def start(paypal: PayPalClient, receipt_id: str, *, guard: bool = False) -> dict[str, Any]:
    """Create the PayPal order for a logged intent. Returns the order, and the approval
    link when the buyer still has to approve.

    guard=True checks the choice against the receipt's confirmed constraints first. On a violation
    no PayPal call is made: a purchase_blocked event is appended and {"blocked": True, ...} returned.
    Scenario runs pass guard=False (an agent platform without a guard) and record that."""
    receipt = ledger.get_receipt(receipt_id) if receipt_id else None
    if receipt is None:
        raise CheckoutError("A PayPal order needs a logged receipt_id; log the intent first")
    chain = ledger.verify_chain()
    if not chain["ok"]:
        raise CheckoutError(f"Ledger failed verification at record {chain['bad_seq']}: {chain['error']}")
    run = receipt["agent_run"]
    if not run.get("chosen_product_id"):
        raise CheckoutError("The agent chose no product, so there is nothing to buy")
    product = get_product(run["chosen_product_id"])
    if product is None:
        raise CheckoutError(f"Unknown product {run['chosen_product_id']!r}")

    if guard:
        found = purchase_guard.violations(receipt["mandate"]["constraints"], product, run.get("chosen_size"))
        if found:
            ledger.append_event(receipt_id, "purchase_blocked", {
                "violations": found, "product_id": product["product_id"], "size": run.get("chosen_size"),
                "price": f"{product['price']:.2f}", "currency": product["currency"], "guard": "on"})
            return {"receipt_id": receipt_id, "blocked": True, "status": "BLOCKED", "violations": found,
                    "product_id": product["product_id"], "amount": f"{product['price']:.2f}",
                    "currency": product["currency"]}

    # Charge the catalog price, never a price the agent reported.
    amount = f"{product['price']:.2f}"
    description = f"{product['name']}, size {run['chosen_size']} ({product['product_id']})"[:127]
    settings = get_settings()
    if settings.paypal_vault_id:
        order = orders.create_vaulted_order(
            paypal, amount, settings.paypal_vault_id, currency=product["currency"],
            description=description, custom_id=receipt_id, invoice_id=receipt["hash"],
            request_id=f"order-{receipt_id}",
        )
    else:
        order = orders.create_order(
            paypal, amount, currency=product["currency"], description=description, custom_id=receipt_id,
            invoice_id=receipt["hash"], return_url=f"{settings.app_base_url}/paypal/return",
            cancel_url=f"{settings.app_base_url}/paypal/cancel", request_id=f"order-{receipt_id}",
        )
    ledger.append_event(
        receipt_id,
        "paypal_order_created",
        {"order_id": order["id"], "status": order["status"], "amount": amount, "currency": product["currency"],
         "vaulted": bool(settings.paypal_vault_id), "invoice_id": receipt["hash"],
         "guard": "on" if guard else "off"},
    )
    result = {"receipt_id": receipt_id, "order_id": order["id"], "status": order["status"],
              "amount": amount, "currency": product["currency"]}
    if order["status"] == "COMPLETED":
        # Vaulted orders come back already captured.
        result["capture_id"] = orders.capture_id(order)
        ledger.append_event(receipt_id, "paypal_captured",
                             {"order_id": order["id"], "status": order["status"], "capture_id": result["capture_id"]})
    else:
        result["approve_url"] = orders.approve_link(order)
    return result


def complete(paypal: PayPalClient, order_id: str) -> dict[str, Any]:
    """Capture an approved order and link the capture to its receipt."""
    captured = orders.capture_order(paypal, order_id)
    result = {"order_id": order_id, "status": captured["status"], "capture_id": orders.capture_id(captured)}
    receipt_id = ledger.find_by_order(order_id)
    if receipt_id:
        ledger.append_event(receipt_id, "paypal_captured", result)
    return {"receipt_id": receipt_id, **result}
