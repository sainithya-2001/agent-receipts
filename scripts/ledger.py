"""Inspect the ledger database (DATABASE_URL).

    python -m scripts.ledger load-staged     # replay scenarios/staged/ receipts (idempotent)
    python -m scripts.ledger verify          # recheck every hash and link
    python -m scripts.ledger show RECEIPT_ID
"""
import json
import sys

from backend import ledger


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "load-staged":
        print(json.dumps({"loaded": ledger.load_staged()}, indent=2))
    elif argv[0] == "verify":
        result = ledger.verify_chain()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    elif argv[0] == "show" and len(argv) == 2:
        receipt = ledger.get_receipt(argv[1])
        if receipt is None:
            print(f"No receipt {argv[1]}")
            return 1
        print(json.dumps(receipt, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
