import asyncio
import json

from mcp import Client

from backend import ledger
from backend.ledger.mcp_server import mcp

LOG_ARGS = {
    "request_text": "Buy me running shoes, size 8, under $120",
    "constraints": {"category": "running shoes", "size": "8", "max_price": 120, "currency": "USD"},
    "options_considered": [{"product_id": "p_03", "price": 109.99, "attrs": {"size": "8"}, "reason": "fits"}],
    "chosen_product_id": "p_03",
    "chosen_size": "8",
    "decision_summary": "Cushioned, under budget.",
    "model": "m",
}


def call(*calls):
    """Run tool calls against the server in-process; returns each result's structured content."""
    async def run():
        async with Client(mcp, raise_exceptions=True) as client:
            out = []
            for name, args in calls:
                result = await client.call_tool(name, args)
                out.append((result.is_error, result.structured_content))
            return out
    return asyncio.run(run())


def tool_schemas():
    async def run():
        async with Client(mcp) as client:
            return (await client.list_tools()).tools
    return asyncio.run(run())


def test_exposes_the_three_ledger_tools():
    tools = tool_schemas()
    assert {t.name for t in tools} == {"log_intent", "get_receipt", "verify_chain"}
    # A field named "reasoning" in a tool schema made Claude refuse; keep it out.
    assert '"reasoning"' not in json.dumps([t.input_schema for t in tools])


def test_log_get_verify_round_trip():
    [(err, logged)] = call(("log_intent", LOG_ARGS))
    assert not err and logged["receipt_id"].startswith("rcpt_")

    [(_, receipt), (_, verified)] = call(("get_receipt", {"receipt_id": logged["receipt_id"]}), ("verify_chain", {}))
    assert receipt["hash"] == logged["hash"]
    assert receipt["agent_run"]["reasoning"] == "Cushioned, under budget."
    assert verified["ok"] is True and verified["records"] == 1
    # Same ledger the API uses.
    assert ledger.get_receipt(logged["receipt_id"])["hash"] == logged["hash"]


def test_errors_come_back_as_tool_errors():
    [(missing_err, _), (invalid_err, _)] = call(
        ("get_receipt", {"receipt_id": "rcpt_0000000000000000"}),
        ("log_intent", {**LOG_ARGS, "request_text": ""}),
    )
    assert missing_err and invalid_err
    assert ledger.verify_chain()["records"] == 0
