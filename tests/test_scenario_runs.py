"""Break-the-agent runs: a real (here mocked) vaulted order per run, a simulated dispute, and
the tamper scenario that only the PayPal anchor catches."""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import ledger, scenarios
from backend.app.main import app
from backend.ledger import store
from backend.ledger.tamper import TamperRefused, rewrite_intent
from tests.test_disputes_flow import fake_claude


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_VAULT_ID", "VAULT1")
    monkeypatch.delenv("FILING_ENABLED", raising=False)


class FakePayPal:
    """Vaulted orders complete at once; GET capture returns what checkout sent (PayPal's copy)."""

    def __init__(self):
        self.captures = {}

    def create_vaulted_order(self, client, amount, vault_id, **kw):
        n = len(self.captures) + 1
        self.captures[f"CAP{n}"] = {"custom_id": kw["custom_id"], "invoice_id": kw["invoice_id"],
                                    "links": [{"rel": "up", "href": f"https://x/v2/checkout/orders/ORD{n}"}]}
        return {"id": f"ORD{n}", "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": f"CAP{n}"}]}}]}

    def get_capture(self, client, capture_id):
        return self.captures[capture_id]


@pytest.fixture
def paypal():
    fake = FakePayPal()
    with patch("backend.checkout.orders.create_vaulted_order", side_effect=fake.create_vaulted_order), \
         patch("backend.ledger.anchor.payments.get_capture", side_effect=fake.get_capture):
        yield fake


def test_scenario_buys_for_real_and_verifies_the_anchor(paypal):
    out = scenarios.run("wrong_size", MagicMock(), client=fake_claude("AGENT_ERROR", "AL-2"))
    assert out["order"]["order_id"] == "ORD1" and out["order"]["capture_id"] == "CAP1"
    assert out["verdict"]["integrity"]["status"] == "verified"
    assert out["verdict"]["fault_class"] == "AGENT_ERROR"
    assert out["dispute_id"].startswith("SIM-")  # the dispute stays simulated
    kinds = [e["kind"] for e in out["receipt"]["events"]]
    assert kinds == ["paypal_order_created", "paypal_captured", "dispute_opened", "dispute_verdict"]
    opened = next(e for e in out["receipt"]["events"] if e["kind"] == "dispute_opened")
    assert opened["simulated"] is True


def test_scenario_without_vault_is_unanchored(monkeypatch, paypal):
    monkeypatch.delenv("PAYPAL_VAULT_ID")
    out = scenarios.run("buyer_remorse", MagicMock(), client=fake_claude())
    assert out["order"] is None and out["verdict"]["integrity"]["status"] == "unanchored"
    assert not paypal.captures


def test_tamper_passes_verify_chain_but_fails_the_anchor(paypal):
    client = fake_claude()
    out = scenarios.run("tampered_receipt", MagicMock(), client=client)
    receipt = out["receipt"]
    assert receipt["mandate"]["constraints"]["size"] == "9"
    assert "size 9" in receipt["mandate"]["request_text"]
    assert ledger.verify_chain()["ok"] is True  # the rewrite is internally consistent
    assert paypal.captures["CAP1"]["invoice_id"] == out["tampered"]["old_hash"] != receipt["hash"]
    created = next(e for e in receipt["events"] if e["kind"] == "paypal_order_created")
    assert created["invoice_id"] == receipt["hash"]  # the ledger's own copy was rewritten too
    v = out["verdict"]
    assert v["status"] == "needs_review" and v["fault_class"] is None
    assert v["integrity"] == {"status": "failed", "check": "anchor", "note": v["integrity"]["note"]}
    assert "invoice_id" in v["integrity"]["note"]
    client.messages.create.assert_not_called()  # integrity failure never reaches the model


def test_tamper_needs_a_real_order(monkeypatch):
    monkeypatch.delenv("PAYPAL_VAULT_ID")
    with pytest.raises(scenarios.ScenarioError):
        scenarios.run("tampered_receipt", MagicMock())


def test_tamper_refuses_staged_and_real_receipts():
    ledger.load_staged()
    with pytest.raises(TamperRefused, match="staged"):
        rewrite_intent("rcpt_627e333bfb7045c1", lambda body: None)
    real = ledger.log_intent(scenarios.get_scenario("buyer_remorse")["mandate"],
                             {**scenarios.get_scenario("buyer_remorse")["agent_run"], "model": "claude-x"})
    with pytest.raises(TamperRefused, match="not a seeded scenario"):
        rewrite_intent(real, lambda body: None)


def test_tamper_refuses_when_other_receipts_follow():
    first = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    other = scenarios.log_receipt(scenarios.get_scenario("buyer_remorse"))
    other_hash = ledger.get_receipt(other)["hash"]
    with pytest.raises(TamperRefused, match="other receipts"):
        rewrite_intent(first, lambda body: body["mandate"]["constraints"].update(size="9"))
    assert ledger.get_receipt(other)["hash"] == other_hash
    assert ledger.get_receipt(first)["mandate"]["constraints"]["size"] == "8"


def test_triggers_are_back_after_a_tamper(paypal):
    scenarios.run("tampered_receipt", MagicMock(), client=fake_claude())
    with pytest.raises(Exception, match="append-only"):
        store.connection().execute("UPDATE records SET body = body")


def test_scenario_list_has_the_tamper_card():
    listed = {s["name"]: s for s in TestClient(app).get("/scenarios").json()}
    assert len(listed) == 6
    assert listed["tampered_receipt"]["expected_status"] == "needs_review"
    assert listed["wrong_size"]["expected_status"] == "decided"


def test_run_route_returns_order_and_verdict(paypal):
    with patch("backend.agents.adjudicator.anthropic.Anthropic", return_value=fake_claude("AGENT_ERROR", "AL-2")):
        r = TestClient(app).post("/scenarios/over_budget/run")
    body = r.json()
    assert r.status_code == 200, body
    assert body["order"]["capture_id"] == "CAP1" and body["verdict"]["integrity"]["status"] == "verified"
    assert json.dumps(body)  # serialisable


def test_guard_card_blocks_the_overspend_before_paypal(paypal):
    pp = MagicMock()
    with patch("backend.checkout.orders.create_vaulted_order") as vaulted, patch("backend.checkout.orders.create_order") as redirect:
        out = scenarios.run("guard_stops_overspend", pp, client=fake_claude())  # no model call needed
    vaulted.assert_not_called(); redirect.assert_not_called()
    assert not pp.method_calls and pp.request.call_count == 0  # PayPal was never touched
    assert out["blocked"] is True and out["dispute_id"] is None and out["status"] == "blocked"
    assert [v["field"] for v in out["violations"]] == ["price"]
    kinds = [e["kind"] for e in out["receipt"]["events"]]
    assert kinds == ["purchase_blocked"]  # no order, no capture, no dispute
    assert out["receipt"]["events"][0]["guard"] == "on" and out["receipt"]["events"][0]["product_id"] == "p_04"
    assert ledger.verify_chain()["ok"]


def test_guard_cards_are_labelled_and_the_old_card_says_no_guard():
    listed = {s["name"]: s for s in TestClient(app).get("/scenarios").json()}
    assert listed["guard_stops_overspend"]["guard"] == "on" and listed["guard_stops_overspend"]["expected_status"] == "blocked"
    assert listed["over_budget"]["guard"] == "off" and "no guard" in listed["over_budget"]["title"].lower()
    assert all(listed[n]["guard"] == "off" for n in ("wrong_size", "prompt_injection", "buyer_remorse", "tampered_receipt"))


def test_guard_card_runs_through_the_route_without_credentials(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID"); monkeypatch.delenv("PAYPAL_CLIENT_SECRET")
    r = TestClient(app).post("/scenarios/guard_stops_overspend/run")
    assert r.status_code == 200 and r.json()["blocked"] is True


def test_unguarded_over_budget_still_buys_for_real(paypal):
    out = scenarios.run("over_budget", MagicMock(), client=fake_claude("AGENT_ERROR", "AL-2"))
    assert out["order"]["capture_id"] == "CAP1"
    assert next(e for e in out["receipt"]["events"] if e["kind"] == "paypal_order_created")["guard"] == "off"
