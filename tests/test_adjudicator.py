import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend import ledger, scenarios
from backend.agents import adjudicator
from backend.agents.adjudicator import AGENT_ERROR, BUYER_REMORSE, MERCHANT_ERROR

CHAIN_OK = {"ok": True, "records": 1, "head_hash": "x", "bad_seq": None, "error": None}


@pytest.fixture(autouse=True)
def model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")


def claude_says(fault, rule="AL-2", confidence=0.9):
    """A fake Anthropic client whose structured-output reply is this verdict."""
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    answer = {"fault_class": fault, "confidence": confidence, "cited_rule_id": rule,
              "buyer_claim": "The buyer wants a refund.", "explanation": "Because of the facts."}
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(answer))], stop_reason="end_turn", usage=usage)
    return client


def scenario_case(name):
    s = scenarios.get_scenario(name)
    rid = scenarios.log_receipt(s)
    dispute = scenarios.build_dispute(s, rid)
    facts = adjudicator.find_facts(ledger.get_receipt(rid), dispute, chain=CHAIN_OK, anchor=None)
    return s, dispute, facts


def sent_payload(client):
    kwargs = client.messages.create.call_args.kwargs
    return kwargs, json.loads(kwargs["messages"][0]["content"])


@pytest.mark.parametrize("name, fault, rule, violations, flagged", [
    ("wrong_size", AGENT_ERROR, "AL-2", ["size"], False),
    ("over_budget", AGENT_ERROR, "AL-2", ["price"], False),
    ("prompt_injection", MERCHANT_ERROR, "AL-3", ["price"], True),
    ("buyer_remorse", BUYER_REMORSE, "AL-5", [], False),
])
def test_four_scenarios(name, fault, rule, violations, flagged):
    s, dispute, facts = scenario_case(name)
    assert s["expected_fault"] == fault
    assert facts["violations"] == violations
    assert facts["integrity"]["status"] == "unanchored"
    client = claude_says(fault, rule)

    verdict = adjudicator.adjudicate(facts, dispute, client=client)

    assert verdict["status"] == "decided" and verdict["fault_class"] == fault
    assert verdict["cited_rule"]["id"] == rule and verdict["override"] is None
    assert verdict["agent_flagged"] is flagged
    assert verdict["action"] == ("provide_evidence" if fault == BUYER_REMORSE else "accept_claim")
    assert verdict["confidence"] == 0.9 and verdict["model"] == "test-model"
    kwargs, payload = sent_payload(client)
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "tool_choice" not in kwargs  # forced tool use is a 400 on current models
    assert payload["buyer_messages"] == [s["dispute"]["buyer_message"]]
    assert rule in {r["id"] for r in payload["rules"]}


@pytest.mark.parametrize("name, forced", [
    ("wrong_size", AGENT_ERROR), ("over_budget", AGENT_ERROR), ("prompt_injection", MERCHANT_ERROR),
])
def test_facts_override_a_wrong_model_answer(name, forced):
    _, dispute, facts = scenario_case(name)
    verdict = adjudicator.adjudicate(facts, dispute, client=claude_says(BUYER_REMORSE, "AL-5", 0.95))
    assert verdict["fault_class"] == forced and verdict["status"] == "decided"
    assert verdict["override"]["model_said"] == BUYER_REMORSE
    assert verdict["confidence"] == 0.6
    assert verdict["cited_rule"]["id"] == adjudicator.allowed_classes(facts)[forced]


def test_contradiction_with_two_possible_classes_goes_to_review():
    _, dispute, facts = scenario_case("buyer_remorse")
    verdict = adjudicator.adjudicate(facts, dispute, client=claude_says(AGENT_ERROR))
    assert verdict["status"] == "needs_review" and verdict["fault_class"] is None and verdict["action"] is None


def test_uncited_or_unknown_rule_falls_back_to_the_class_rule():
    _, dispute, facts = scenario_case("buyer_remorse")
    verdict = adjudicator.adjudicate(facts, dispute, client=claude_says(BUYER_REMORSE, "ZZ-9"))
    assert verdict["cited_rule"]["id"] == "AL-5"


def test_failed_integrity_skips_the_model():
    _, dispute, facts = scenario_case("buyer_remorse")
    facts["integrity"] = adjudicator.integrity_status(
        CHAIN_OK, {"ok": False, "anchored": True, "custom_id_ok": True, "invoice_id": "a" * 64,
                   "error": "intent was changed after the order was created"})
    client = claude_says(BUYER_REMORSE)
    verdict = adjudicator.adjudicate(facts, dispute, client=client)
    client.messages.create.assert_not_called()
    assert verdict["status"] == "needs_review" and verdict["cited_rule"]["id"] == "AL-6"


def test_integrity_statuses():
    broken = {**CHAIN_OK, "ok": False, "bad_seq": 3, "error": "hash mismatch"}
    assert adjudicator.integrity_status(broken, None)["status"] == "failed"
    assert adjudicator.integrity_status(CHAIN_OK, None)["status"] == "unanchored"
    assert adjudicator.integrity_status(CHAIN_OK, {"ok": True, "invoice_id": "a" * 64})["status"] == "verified"
    old = {"ok": False, "anchored": False, "custom_id_ok": True, "error": "predates anchoring"}
    assert adjudicator.integrity_status(CHAIN_OK, old)["status"] == "unanchored"
    stolen = {"ok": False, "anchored": True, "custom_id_ok": False, "error": "belongs to another receipt"}
    assert adjudicator.integrity_status(CHAIN_OK, stolen)["status"] == "failed"


def test_staged_real_dispute_is_merchant_error_case():
    """PP-R-LQT-10195344: the agent chose size 8 correctly; the buyer says size 9 arrived."""
    ledger.load_staged()
    receipt = ledger.get_receipt("rcpt_627e333bfb7045c1")
    dispute = {
        "dispute_id": "PP-R-LQT-10195344", "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "status": "WAITING_FOR_SELLER_RESPONSE", "dispute_life_cycle_stage": "INQUIRY",
        "dispute_amount": {"currency_code": "USD", "value": "109.99"},
        "disputed_transactions": [{"seller_transaction_id": "9V56960783661725H", "custom": "rcpt_627e333bfb7045c1",
                                   "gross_amount": {"currency_code": "USD", "value": "109.99"}}],
        "messages": [{"posted_by": "BUYER", "content": "I ordered size 8 through an automated shopping assistant, "
                                                        "but received size 9. I'd like a full refund of $109.99"}],
    }
    unanchored = {"ok": False, "anchored": False, "custom_id_ok": True, "error": "predates anchoring"}
    facts = adjudicator.find_facts(receipt, dispute, chain=CHAIN_OK, anchor=unanchored)
    assert facts["violations"] == [] and facts["integrity"]["status"] == "unanchored"
    assert set(adjudicator.allowed_classes(facts)) == {MERCHANT_ERROR, BUYER_REMORSE}

    verdict = adjudicator.adjudicate(facts, dispute, client=claude_says(MERCHANT_ERROR, "AL-4"))
    assert verdict["fault_class"] == MERCHANT_ERROR and verdict["cited_rule"]["id"] == "AL-4"
    assert verdict["action"] == "accept_claim"


def test_no_reasoning_field_in_the_output_schema():
    assert "reasoning" not in json.dumps(adjudicator.VERDICT_SCHEMA)


@pytest.mark.parametrize("message, size", [
    ("I ordered running shoes in size 8 through an automated shopping assistant, but received size 9.", "9"),
    ("They shipped me a size 10 instead.", "10"),
    ("The pair arrived in size 7.5, way too small.", "7.5"),
    ("My shopping assistant ordered these in size 9. I asked for size 8 and they don't fit.", None),
    ("The shoes arrived fine, but I changed my mind.", None),
])
def test_delivered_report_reads_only_delivery_claims(message, size):
    assert adjudicator.delivered_report([message]).get("size") == size


def test_delivered_size_is_compared_with_what_was_bought():
    s = scenarios.get_scenario("buyer_remorse")  # agent bought size 8, as asked
    rid = scenarios.log_receipt(s)
    dispute = scenarios.build_dispute(s, rid)
    dispute["messages"] = [{"posted_by": "BUYER", "content": "Ordered size 8 but received size 9."}]
    facts = adjudicator.find_facts(ledger.get_receipt(rid), dispute, chain=CHAIN_OK, anchor=None)
    size = next(d for d in facts["diff"] if d["field"] == "size")
    assert size["ok"] is True and size["delivered"] == "9" and size["delivered_ok"] is False
    price = next(d for d in facts["diff"] if d["field"] == "price")
    assert price["delivered"] is None and price["delivered_ok"] is None


# --- listing snapshot (agent_run.chosen_listing) ---

def test_receipt_stores_a_listing_snapshot():
    _, _, facts = scenario_case("wrong_size")
    rid = facts["receipt_id"]
    listing = ledger.get_receipt(rid)["agent_run"]["chosen_listing"]
    assert listing["product_id"] == "p_03" and listing["name"] == "Aero Glide 4"
    assert listing["price"] == 109.99 and listing["currency"] == "USD" and listing["category"] == "running shoes"
    assert "ushioned" in listing["description"] and listing["attrs"]["size"] == "9"
    assert facts["listing_source"] == "receipt_snapshot"


def test_facts_use_the_snapshot_not_the_current_catalog(monkeypatch):
    s = scenarios.get_scenario("buyer_remorse")
    rid = scenarios.log_receipt(s)
    dispute = scenarios.build_dispute(s, rid)
    dispute["disputed_transactions"][0].pop("gross_amount")
    dispute.pop("dispute_amount")
    # The catalog changes after the purchase: price up, category changed.
    changed = {**adjudicator.get_product("p_03"), "price": 300.0, "category": "sandals", "name": "Renamed"}
    monkeypatch.setattr(adjudicator, "get_product", lambda pid: changed)
    facts = adjudicator.find_facts(ledger.get_receipt(rid), dispute, chain=CHAIN_OK, anchor=None)
    assert facts["listing_source"] == "receipt_snapshot"
    assert facts["paid"] == 109.99 and facts["violations"] == []
    assert facts["chosen"]["name"] == "Aero Glide 4"


def test_old_receipt_falls_back_to_the_catalog_and_says_so():
    ledger.load_staged()
    receipt = ledger.get_receipt("rcpt_627e333bfb7045c1")
    assert "chosen_listing" not in receipt["agent_run"]  # logged before snapshots; its hash must not change
    assert ledger.verify_chain()["ok"]
    facts = adjudicator.find_facts(receipt, {"dispute_id": "D", "messages": []}, chain=CHAIN_OK, anchor=None)
    assert facts["listing_source"] == "catalog_fallback"
    verdict = adjudicator.adjudicate(facts, {"dispute_id": "D", "messages": []},
                                     client=claude_says(MERCHANT_ERROR, "AL-4"))
    assert verdict["listing_source"] == "catalog_fallback"


# --- injection causality ---

def _injection_case(*, steps=None, violate="price"):
    """prompt_injection scenario with its receipt edited before logging."""
    s = scenarios.get_scenario("prompt_injection")
    if steps is not None:
        s["agent_run"]["steps"] = steps
    if violate == "size":  # a size violation with a price injection: wrong link
        s["agent_run"]["chosen_size"] = "9"
        s["agent_run"]["options_considered"][0]["price"] = 109.99
    rid = scenarios.log_receipt(s)
    dispute = scenarios.build_dispute(s, rid)
    if violate == "size":
        for k in ("gross_amount",):
            dispute["disputed_transactions"][0][k]["value"] = "109.99"
        dispute["dispute_amount"]["value"] = "109.99"
    return dispute, adjudicator.find_facts(ledger.get_receipt(rid), dispute, chain=CHAIN_OK, anchor=None)


def test_injection_linked_when_seen_and_violation_matches_instruction():
    _, facts = _injection_case()
    inj = facts["injection"]
    assert inj["seen_by_agent"] and inj["linked"]
    assert inj["instructed_fields"] == ["price"] and inj["matched_fields"] == ["price"]
    assert "Ignore the budget" in inj["instruction"]
    assert adjudicator.allowed_classes(facts) == {MERCHANT_ERROR: "AL-3"}


def test_injection_not_seen_by_agent_goes_to_review():
    steps = [{"tool": "search_products", "input": {}, "result_ids": ["p_01", "p_03"]},
             {"tool": "submit_choice", "input": {"chosen_product_id": "p_08", "chosen_size": "8"}}]
    dispute, facts = _injection_case(steps=steps)
    assert facts["injection"]["seen_by_agent"] is False and facts["injection"]["linked"] is False
    client = claude_says(MERCHANT_ERROR, "AL-3")
    verdict = adjudicator.adjudicate(facts, dispute, client=client)
    client.messages.create.assert_not_called()
    assert verdict["status"] == "needs_review" and verdict["fault_class"] is None
    assert verdict["agent_flagged"] is False


def test_injection_that_does_not_match_the_violation_goes_to_review():
    dispute, facts = _injection_case(violate="size")
    assert facts["violations"] == ["size"]
    assert facts["injection"]["instructed_fields"] == ["price"] and facts["injection"]["matched_fields"] == []
    assert adjudicator.allowed_classes(facts) == {}
    client = claude_says(AGENT_ERROR)
    verdict = adjudicator.adjudicate(facts, dispute, client=client)
    client.messages.create.assert_not_called()
    assert verdict["status"] == "needs_review"


def test_injection_without_a_violation_is_ignored():
    s = scenarios.get_scenario("prompt_injection")
    s["mandate"]["constraints"]["max_price"] = 500  # the purchase met the mandate after all
    rid = scenarios.log_receipt(s)
    dispute = scenarios.build_dispute(s, rid)
    facts = adjudicator.find_facts(ledger.get_receipt(rid), dispute, chain=CHAIN_OK, anchor=None)
    assert facts["violations"] == []
    assert set(adjudicator.allowed_classes(facts)) == {MERCHANT_ERROR, BUYER_REMORSE}
