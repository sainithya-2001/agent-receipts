"""The receipt is hash-chained and PayPal-anchored, not cryptographically signed."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "backend/agents/adjudicator.py",
    "backend/ledger/mcp_server.py",
    "frontend/src/app/layout.tsx",
    "frontend/src/app/page.tsx",
    "frontend/src/app/receipts/[id]/page.tsx",
    "frontend/src/api/timeline.ts",
]


def test_no_signed_claims_about_our_receipt():
    for rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        # code identifiers such as `type: "signed"` / `case "signed"` are allowed
        text = re.sub(r'["\']signed["\']', "", text)
        assert not re.search(r"\bsign(ed|s|ing)?\b", text, re.I), rel
