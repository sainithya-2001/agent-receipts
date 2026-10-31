"""Work with disputes from the command line (Claude + PayPal sandbox).

    python -m scripts.disputes list
    python -m scripts.disputes resolve DISPUTE_ID          # adjudicate only; PayPal is not changed
    python -m scripts.disputes resolve DISPUTE_ID --file   # also take the next PayPal step
    python -m scripts.disputes scenario NAME               # seeded demo failure; real vaulted order, simulated dispute
    python -m scripts.disputes scenario all

resolve is safe to repeat: a dispute is adjudicated once and each PayPal step is filed once.
"""
import json
import logging
import sys

from backend import ledger, resolve, scenarios
from backend.paypal import disputes
from backend.paypal.client import PayPalClient, PayPalError

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")


def _card(result: dict) -> dict:
    v = result.get("verdict") or {}
    return {"dispute_id": result["dispute_id"], "receipt_id": result.get("receipt_id"), "status": result["status"],
            "fault_class": v.get("fault_class"), "confidence": v.get("confidence"),
            "cited_rule": (v.get("cited_rule") or {}).get("id"), "integrity": (v.get("integrity") or {}).get("status"),
            "violations": v.get("violations"), "agent_flagged": v.get("agent_flagged"), "override": v.get("override"),
            "outcome": v.get("outcome"), "explanation": v.get("explanation"),
            "next": result.get("next"), "filed": result.get("filed")}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    ledger.load_staged()  # as the API does at startup: staged disputes need their receipts
    try:
        if argv[0] == "list":
            for d in disputes.list_disputes(PayPalClient()).get("items", []):
                print(d["dispute_id"], d.get("status"), d.get("dispute_life_cycle_stage"), d.get("reason"))
        elif argv[0] == "resolve" and len(argv) >= 2:
            result = resolve.resolve_by_id(PayPalClient(), argv[1], file="--file" in argv[2:])
            print(json.dumps(_card(result), indent=2))
        elif argv[0] == "scenario" and len(argv) == 2:
            names = [s["name"] for s in scenarios.list_scenarios()] if argv[1] == "all" else [argv[1]]
            for name in names:
                if scenarios.get_scenario(name) is None:
                    print(f"Unknown scenario {name!r}")
                    return 2
                result = scenarios.run(name, PayPalClient())  # real vaulted order when PAYPAL_VAULT_ID is set
                print(json.dumps({"scenario": name, "expected": result["expected_fault"] or result["expected_status"],
                                  "order_id": (result["order"] or {}).get("order_id"), **_card(result)}, indent=2))
        else:
            print(__doc__)
            return 2
    except PayPalError as exc:
        print(f"FAILED: {exc}\n{json.dumps(exc.body, indent=2)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
