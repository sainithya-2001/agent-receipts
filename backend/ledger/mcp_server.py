"""MCP server for the receipts ledger: log_intent, get_receipt, verify_chain.

    python -m backend.ledger.mcp_server          # stdio, for Claude Code / Claude Desktop

It uses the same database as the API server (DATABASE_URL), so receipts logged by either
are visible to both.
"""
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from backend import ledger
from backend.ledger.models import Constraints, OptionConsidered

mcp = MCPServer(
    name="agent-receipts-ledger",
    instructions=(
        "A tamper-evident ledger of what shopping agents were asked to buy and what they chose. "
        "Call log_intent before creating any payment; a PayPal order needs the receipt_id it returns. "
        "Use get_receipt to read a receipt and its PayPal events, and verify_chain to check that no "
        "record has been altered."
    ),
)


# The choice explanation is named decision_summary, not "reasoning": that field name in a tool
# schema made Claude refuse as reasoning extraction (see shopping.py). It's stored as reasoning.
@mcp.tool()
def log_intent(
    request_text: str = Field(description="The person's request, word for word."),
    constraints: Constraints = Field(description="Category, size, max_price and currency from the request."),
    options_considered: list[OptionConsidered] = Field(
        description="Every product seriously considered, with why it was or wasn't chosen."),
    chosen_product_id: str | None = Field(description="The product to buy, or null if none fits."),
    chosen_size: str | None = Field(description="The size to buy, or null."),
    decision_summary: str = Field(description="One or two sentences: why this product fits the request."),
    model: str = Field(description="The model that made the decision."),
) -> dict[str, Any]:
    """Log a signed receipt of intent before buying. Returns the receipt_id and its hash."""
    receipt_id = ledger.log_intent(
        {"request_text": request_text, "constraints": constraints.model_dump()},
        {
            "model": model,
            "options_considered": [o.model_dump() for o in options_considered],
            "chosen_product_id": chosen_product_id,
            "chosen_size": chosen_size,
            "reasoning": decision_summary,
        },
    )
    receipt = ledger.get_receipt(receipt_id)
    return {"receipt_id": receipt_id, "hash": receipt["hash"], "prev_hash": receipt["prev_hash"]}


@mcp.tool()
def get_receipt(receipt_id: str) -> dict[str, Any]:
    """A receipt of intent and the PayPal events linked to it, in chain order."""
    receipt = ledger.get_receipt(receipt_id)
    if receipt is None:
        raise ValueError(f"No receipt {receipt_id!r}")
    return receipt


@mcp.tool()
def verify_chain() -> dict[str, Any]:
    """Recheck every hash and link in the ledger. ok is false, with bad_seq and error, if any
    record was altered, removed or reordered."""
    return ledger.verify_chain()


def main() -> None:
    ledger.load_staged()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
