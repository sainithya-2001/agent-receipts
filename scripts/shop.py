"""Run the buy flow against the real APIs (Claude + PayPal sandbox).

    python -m scripts.shop "Buy me running shoes, size 8, under $120"
    python -m scripts.shop capture ORDER_ID      # after the sandbox buyer approves

Receipts go to the ledger database (DATABASE_URL), so `capture` links back to the receipt
just as `/paypal/return` does.
"""
import json
import logging
import sys

from backend import checkout, shop
from backend.paypal.client import PayPalClient, PayPalError

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    paypal = PayPalClient()
    try:
        if argv[0] == "capture":
            print(json.dumps(checkout.complete(paypal, argv[1]), indent=2))
        else:
            print(json.dumps(shop.buy(" ".join(argv), paypal), indent=2))
    except PayPalError as exc:
        print(f"FAILED: {exc}\n{json.dumps(exc.body, indent=2)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
