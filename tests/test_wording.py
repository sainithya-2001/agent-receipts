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


def test_agent_error_outcome_claims_no_transfer_of_liability():
    """AGENT_ERROR refunds the buyer and names the provider; it doesn't say liability is transferred."""
    from backend.agents.adjudicator import AGENT_ERROR, OUTCOMES

    text = OUTCOMES[AGENT_ERROR][0]
    assert text == ("Refund the buyer; the agent provider is identified as responsible for the mandate breach. "
                    "Recovering costs from the provider is a separate process.")
    files = ["backend/agents/adjudicator.py", "backend/rag/docs/agent-liability.md", "README.md", "CLAUDE.md",
             "frontend/src/api/explain.ts"]
    for rel in files:
        body = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"liability (passes|transfers|shifts)|provider is liable|liability to agent provider",
                             body, re.I), rel
