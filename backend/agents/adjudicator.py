"""Adjudicator agent: decides who is at fault when an agent purchase is disputed.

Code establishes the facts: whether the receipt can be trusted, the diff between what the
mandate asked for and what was bought, and whether the chosen listing tried to instruct
shopping agents. Those facts limit which fault classes are possible. Claude reads the buyer's
message and the retrieved rules, picks the class, cites a rule, and explains it for the
verdict card. If Claude picks a class the facts rule out, the facts win and the override is
recorded, so the four demo scenarios can't be flipped by the model.
"""
import json
import logging
import re
from typing import Any

import anthropic

from backend.agents.llm import create_message
from backend.catalog import get_product
from backend.config import get_settings
from backend.rag.ingest import get_rule
from backend.rag.retrieve import search

log = logging.getLogger(__name__)

AGENT_ERROR, MERCHANT_ERROR, BUYER_REMORSE = "AGENT_ERROR", "MERCHANT_ERROR", "BUYER_REMORSE"
FAULT_CLASSES = (AGENT_ERROR, MERCHANT_ERROR, BUYER_REMORSE)

# What each class means for the money, and which PayPal response follows from it.
OUTCOMES = {
    AGENT_ERROR: ("Refund the buyer; liability passes to the agent provider.", "accept_claim"),
    MERCHANT_ERROR: ("Refund the buyer; the merchant is at fault.", "accept_claim"),
    BUYER_REMORSE: ("Merchant submits the receipt as evidence and should win.", "provide_evidence"),
}

# Listing text addressed to agents rather than describing the product.
_INJECTION = re.compile(
    r"system note|shopping assistants?|\bignore (the |your |all |any )?(budget|instructions|constraints|rules|limit)"
    r"|budget is (a |only a )?soft|what the user really wants",
    re.IGNORECASE,
)

SYSTEM = """You are the adjudicator for disputed purchases made by AI shopping agents. A buyer has disputed a payment through PayPal. You decide who is at fault.

You are given:
- The facts, established by code from the hash-anchored receipt of intent and PayPal: what the buyer asked for (the mandate), what the agent chose, what was charged, a field-by-field diff, whether the product listing contained text aimed at shopping agents, and whether the receipt passed its integrity checks. Treat these as settled.
- allowed_fault_classes: the classes the facts leave open. Choose one of them.
- Diff rows may carry delivered: what the buyer says arrived, parsed from their message. It is the buyer's report, not a settled fact; delivered_ok compares it with what was bought.
- The buyer's messages from the dispute. They are the buyer's account and may be mistaken; use them to understand the complaint, not to overturn the facts.
- Rules retrieved from the dispute rulebook. Cite the one rule your decision rests on, by its id.

Fault classes:
- AGENT_ERROR: the purchase broke the mandate (wrong size, over budget, wrong category).
- MERCHANT_ERROR: the listing manipulated the agent into breaking the mandate, or the purchase met the mandate but the merchant delivered something different from what was ordered.
- BUYER_REMORSE: the purchase met the mandate and the buyer reports no delivery problem; they changed their mind.

Give a confidence between 0 and 1. Write the explanation for the buyer and merchant: two or three plain sentences naming the constraint involved and what happened. Summarize the buyer's claim in one sentence."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "fault_class": {"type": "string", "enum": list(FAULT_CLASSES)},
        "confidence": {"type": "number"},
        "cited_rule_id": {"type": "string"},
        "buyer_claim": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["fault_class", "confidence", "cited_rule_id", "buyer_claim", "explanation"],
    "additionalProperties": False,
}


class AdjudicationError(RuntimeError):
    pass


def _money(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def listing_injection(description: str) -> str | None:
    """The part of a listing addressed to shopping agents (the sentences that match), if any."""
    sentences = [m for m in re.finditer(r"[^.!?]+[.!?]*", description or "") if _INJECTION.search(m.group(0))]
    if not sentences:
        return None
    return description[sentences[0].start():sentences[-1].end()].strip()


# "received size 9", "they shipped me a size 9", "arrived in size 9": what the buyer says was
# delivered. Only a delivery verb counts, so "ordered these in size 9" is not a delivery report.
_DELIVERED_SIZE = re.compile(
    r"\b(?:received|got|arrived|delivered|shipped|sent)\b[^.!?]{0,40}?\bsize\s+(\d+(?:\.\d+)?)", re.IGNORECASE)


def delivered_report(messages: list[str]) -> dict[str, str]:
    """What the buyer says arrived, by diff field. Their report, not a verified fact."""
    for text in messages:
        match = _DELIVERED_SIZE.search(text)
        if match:
            return {"size": match.group(1)}
    return {}


def buyer_messages(dispute: dict[str, Any]) -> list[str]:
    return [m["content"] for m in dispute.get("messages") or [] if m.get("posted_by") == "BUYER" and m.get("content")]


def integrity_status(chain: dict[str, Any], anchor: dict[str, Any] | None) -> dict[str, Any]:
    """verified / unanchored / failed. Only "failed" stops adjudication (rule AL-6).

    check says which check decided it: "chain" (the ledger itself) or "anchor" (PayPal's copy)."""
    if not chain["ok"]:
        return {"status": "failed", "check": "chain",
                "note": f"Ledger chain broken at record {chain['bad_seq']}: {chain['error']}"}
    if anchor is None:
        return {"status": "unanchored", "check": None, "note": "No PayPal capture to check the receipt hash against."}
    if anchor["ok"]:
        return {"status": "verified", "check": "anchor",
                "note": f"Receipt hash matches PayPal's invoice_id ({anchor['invoice_id'][:12]}...)."}
    if anchor["custom_id_ok"] and not anchor["anchored"]:
        return {"status": "unanchored", "check": "anchor",
                "note": "Order predates anchoring: PayPal holds no receipt hash to compare."}
    return {"status": "failed", "check": "anchor", "note": anchor["error"]}


def find_facts(receipt: dict[str, Any], dispute: dict[str, Any], *, chain: dict[str, Any],
               anchor: dict[str, Any] | None) -> dict[str, Any]:
    """The deterministic part: everything the verdict rests on that code can check."""
    constraints = receipt["mandate"]["constraints"]
    run = receipt["agent_run"]
    product = get_product(run["chosen_product_id"]) if run.get("chosen_product_id") else None
    txn = (dispute.get("disputed_transactions") or [{}])[0]
    charged = txn.get("gross_amount") or dispute.get("dispute_amount") or {}
    paid = _money(charged.get("value"))
    if paid is None and product is not None:
        paid = product["price"]

    diff = []
    if constraints.get("size"):
        diff.append({"field": "size", "asked": constraints["size"], "bought": run.get("chosen_size"),
                     "ok": run.get("chosen_size") == constraints["size"]})
    if constraints.get("max_price") is not None:
        diff.append({"field": "price", "asked": f"at most {constraints['max_price']:.2f}",
                     "bought": None if paid is None else f"{paid:.2f}",
                     "ok": paid is not None and paid <= constraints["max_price"]})
    if constraints.get("category"):
        bought = product["category"] if product else None
        diff.append({"field": "category", "asked": constraints["category"], "bought": bought,
                     "ok": bool(bought) and bought.lower() == constraints["category"].lower()})
    if constraints.get("currency") and charged.get("currency_code"):
        diff.append({"field": "currency", "asked": constraints["currency"], "bought": charged["currency_code"],
                     "ok": charged["currency_code"] == constraints["currency"]})

    delivered = delivered_report(buyer_messages(dispute))
    for row in diff:
        row["delivered"] = delivered.get(row["field"])
        row["delivered_ok"] = None if row["delivered"] is None else row["delivered"] == row["bought"]
    match = listing_injection(product["description"]) if product else None
    violations = [d["field"] for d in diff if not d["ok"]]
    return {
        "receipt_id": receipt["receipt_id"],
        "request_text": receipt["mandate"]["request_text"],
        "constraints": constraints,
        "chosen": {"product_id": run.get("chosen_product_id"), "name": product["name"] if product else None,
                   "size": run.get("chosen_size"), "listed_price": product["price"] if product else None},
        "agent_summary": run.get("reasoning"),
        "paid": paid,
        "diff": diff,
        "violations": violations,
        "listing_injection": product["description"] if match else None,
        "integrity": integrity_status(chain, anchor),
        "dispute": {"dispute_id": dispute.get("dispute_id"), "reason": dispute.get("reason"),
                    "status": dispute.get("status"), "stage": dispute.get("dispute_life_cycle_stage")},
    }


def allowed_classes(facts: dict[str, Any]) -> dict[str, str]:
    """The fault classes the facts allow, each with the liability rule that defines it."""
    if facts["violations"] and facts["listing_injection"]:
        return {MERCHANT_ERROR: "AL-3"}
    if facts["violations"]:
        return {AGENT_ERROR: "AL-2"}
    return {MERCHANT_ERROR: "AL-4", BUYER_REMORSE: "AL-5"}


def _rules_for(facts: dict[str, Any], buyer_text: str, required: list[str]) -> list[dict[str, Any]]:
    query = " ".join([facts["dispute"]["reason"] or "", buyer_text, *facts["violations"],
                      "listing instructs shopping assistants" if facts["listing_injection"] else "",
                      "purchase matches mandate" if not facts["violations"] else "breaks the mandate"])
    rules = {r["id"]: r for r in search(query, k=4)}
    for rule_id in ["AL-1", *required] + (["AL-6"] if facts["integrity"]["status"] != "verified" else []):
        rules.setdefault(rule_id, get_rule(rule_id))
    return [{k: r[k] for k in ("id", "title", "text")} for r in rules.values()]


def adjudicate(facts: dict[str, Any], dispute: dict[str, Any],
               client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    """Return the verdict card for a dispute, given its facts from find_facts()."""
    base = {"receipt_id": facts["receipt_id"], "dispute_id": facts["dispute"]["dispute_id"],
            "diff": facts["diff"], "violations": facts["violations"], "integrity": facts["integrity"],
            "agent_flagged": bool(facts["violations"] and facts["listing_injection"])}
    if facts["integrity"]["status"] == "failed":
        rule = get_rule("AL-6")
        return {**base, "status": "needs_review", "fault_class": None, "confidence": None, "action": None,
                "outcome": "Manual review: the receipt failed its integrity check and can't be relied on.",
                "cited_rule": {k: rule[k] for k in ("id", "title", "text")},
                "explanation": facts["integrity"]["note"], "buyer_claim": None, "override": None, "model": None}

    allowed = allowed_classes(facts)
    messages = buyer_messages(dispute)
    rules = _rules_for(facts, " ".join(messages), list(allowed.values()))
    payload = {"facts": facts, "allowed_fault_classes": list(allowed), "buyer_messages": messages, "rules": rules}
    response = create_message(
        client or anthropic.Anthropic(), system=SYSTEM, max_tokens=4000,
        messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
    )
    if response.stop_reason == "max_tokens":
        raise AdjudicationError("Adjudicator hit max_tokens before finishing")
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        answer = json.loads(text or "")
    except ValueError as exc:
        raise AdjudicationError(f"Adjudicator returned no verdict JSON: {(text or '')[:200]}") from exc

    fault, confidence, override = answer["fault_class"], answer["confidence"], None
    cited = answer["cited_rule_id"]
    if fault not in allowed:
        override = {"model_said": fault, "reason": f"Facts allow only {list(allowed)}"}
        log.warning("adjudicator override %s: model said %s, facts allow %s", base["dispute_id"], fault, list(allowed))
        if len(allowed) > 1:
            # The facts don't settle it either; don't guess between the remaining classes.
            return {**base, "status": "needs_review", "fault_class": None, "confidence": None, "action": None,
                    "outcome": "Manual review: the adjudicator's answer contradicted the facts.",
                    "cited_rule": None, "explanation": answer["explanation"], "buyer_claim": answer["buyer_claim"],
                    "override": override, "model": get_settings().anthropic_model}
        fault, confidence = next(iter(allowed)), min(confidence, 0.6)
    if override or cited not in {r["id"] for r in rules}:
        cited = allowed[fault]
    rule = get_rule(cited)
    outcome, action = OUTCOMES[fault]
    return {**base, "status": "decided", "fault_class": fault,
            "confidence": round(max(0.0, min(1.0, float(confidence))), 2), "action": action, "outcome": outcome,
            "cited_rule": {k: rule[k] for k in ("id", "title", "text")}, "explanation": answer["explanation"],
            "buyer_claim": answer["buyer_claim"], "override": override, "model": get_settings().anthropic_model}
