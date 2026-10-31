"""Save the sandbox buyer's PayPal wallet once, for redirect-free agent purchases.

    python -m scripts.vault setup               # prints a link: approve it as the sandbox buyer (no charge)
    python -m scripts.vault finish SETUP_TOKEN  # prints the vault id: put it in .env as PAYPAL_VAULT_ID
"""
import json
import logging
import sys

from backend.paypal import vault
from backend.paypal.client import PayPalClient, PayPalError

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("setup", "finish"):
        print(__doc__)
        return 2
    client = PayPalClient()
    try:
        if argv[0] == "setup":
            token = vault.create_setup_token(client)
            print(f"setup_token={token['id']} status={token['status']}")
            print(f"approve as sandbox buyer: {vault.approve_link(token)}")
            print(f"then: python -m scripts.vault finish {token['id']}")
        else:
            payment_token = vault.create_payment_token(client, argv[1])
            print(f"PAYPAL_VAULT_ID={payment_token['id']}")
    except PayPalError as exc:
        print(f"FAILED: {exc}\n{json.dumps(exc.body, indent=2)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
