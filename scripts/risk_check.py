"""Day 1 risk check against the PayPal sandbox, one step per subcommand.

    python -m scripts.risk_check auth
    python -m scripts.risk_check order [amount]       # prints approve link for the sandbox buyer
    python -m scripts.risk_check capture ORDER_ID
    python -m scripts.risk_check disputes             # list disputes on the merchant account
    python -m scripts.risk_check dispute DISPUTE_ID
    python -m scripts.risk_check escalate DISPUTE_ID
    python -m scripts.risk_check evidence DISPUTE_ID
    python -m scripts.risk_check adjudicate DISPUTE_ID buyer|seller
"""
import json
import logging
import sys

from backend.paypal import disputes, orders
from backend.paypal.client import PayPalClient, PayPalError

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")


def show(obj) -> None:
    print(json.dumps(obj, indent=2))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    client = PayPalClient()
    try:
        if cmd == "auth":
            client.access_token()
            print("auth ok")
        elif cmd == "order":
            order = orders.create_order(
                client, args[0] if args else "109.99", description="Risk check: running shoes size 8"
            )
            print(f"order_id={order['id']} status={order['status']}")
            print(f"approve as sandbox buyer: {orders.approve_link(order)}")
        elif cmd == "capture":
            captured = orders.capture_order(client, args[0])
            print(f"status={captured['status']} capture_id={orders.capture_id(captured)}")
        elif cmd == "disputes":
            for d in disputes.list_disputes(client).get("items", []):
                print(d["dispute_id"], d.get("status"), d.get("reason"), d.get("dispute_amount"))
        elif cmd == "dispute":
            show(disputes.get_dispute(client, args[0]))
        elif cmd == "escalate":
            show(disputes.escalate(client, args[0], "Escalating for review (risk check)."))
        elif cmd == "evidence":
            show(disputes.provide_evidence(client, args[0], "Item shipped matches the buyer's order."))
        elif cmd == "adjudicate":
            outcome = {"buyer": disputes.BUYER_FAVOUR, "seller": disputes.SELLER_FAVOUR}[args[1]]
            show(disputes.adjudicate(client, args[0], outcome))
        else:
            print(__doc__)
            return 2
    except PayPalError as exc:
        print(f"FAILED: {exc}")
        show(exc.body)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
