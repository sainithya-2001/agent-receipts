"""Pre-payment guard: a choice that breaks the confirmed limits never reaches PayPal."""
from unittest.mock import patch

import pytest

from backend import checkout, ledger, shop

MANDATE = {"request_text": "shoes", "constraints": {"category": "running shoes", "size": "8", "max_price": 120,
                                                     "currency": "USD"}}


def run_of(pid, size="8"):
    return {"model": "m", "options_considered": [], "chosen_product_id": pid, "chosen_size": size,
            "reasoning": "r", "steps": []}


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    monkeypatch.delenv("PAYPAL_VAULT_ID", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "http://test")


ORDER = {"id": "ORD1", "status": "PAYER_ACTION_REQUIRED", "links": [{"rel": "payer-action", "href": "x"}]}


def test_blocks_over_budget_without_calling_paypal():
    rid = ledger.log_intent(MANDATE, run_of("p_04"))  # p_04 is over the $120 budget
    with patch("backend.checkout.orders.create_order") as create, patch("backend.checkout.orders.create_vaulted_order") as vaulted:
        out = checkout.start(None, rid, guard=True)
    create.assert_not_called(); vaulted.assert_not_called()
    assert out["blocked"] is True and out["status"] == "BLOCKED"
    assert [v["field"] for v in out["violations"]] == ["price"]
    event = ledger.get_receipt(rid)["events"][-1]
    assert event["kind"] == "purchase_blocked" and event["product_id"] == "p_04" and event["guard"] == "on"
    assert event["violations"][0]["attempted"] != "" and event["price"]
    assert ledger.verify_chain()["ok"]


def test_blocks_wrong_size_without_calling_paypal():
    rid = ledger.log_intent(MANDATE, run_of("p_03", size="9"))
    with patch("backend.checkout.orders.create_order") as create:
        out = checkout.start(None, rid, guard=True)
    create.assert_not_called()
    assert out["blocked"] and [v["field"] for v in out["violations"]] == ["size"]
    assert out["violations"][0] == {"field": "size", "asked": "8", "attempted": "9"}


def test_blocks_wrong_currency_and_category():
    rid = ledger.log_intent({"request_text": "x", "constraints": {"currency": "EUR", "category": "sandals"}}, run_of("p_03"))
    with patch("backend.checkout.orders.create_order") as create:
        out = checkout.start(None, rid, guard=True)
    create.assert_not_called()
    assert {v["field"] for v in out["violations"]} == {"currency", "category"}


def test_allows_in_mandate_purchase():
    rid = ledger.log_intent(MANDATE, run_of("p_03"))
    with patch("backend.checkout.orders.create_order", return_value=ORDER) as create:
        out = checkout.start(None, rid, guard=True)
    create.assert_called_once()
    assert "blocked" not in out and out["order_id"] == "ORD1"
    created = ledger.get_receipt(rid)["events"][0]
    assert created["kind"] == "paypal_order_created" and created["guard"] == "on"


def test_guard_off_records_it_and_still_orders():
    rid = ledger.log_intent(MANDATE, run_of("p_04"))  # violates, but this platform has no guard
    with patch("backend.checkout.orders.create_order", return_value=ORDER) as create:
        out = checkout.start(None, rid)
    create.assert_called_once()
    assert "blocked" not in out
    assert ledger.get_receipt(rid)["events"][0]["guard"] == "off"


def test_shop_buy_with_guard_blocks_and_reports(monkeypatch):
    decision = {"mandate": {**MANDATE}, "agent_run": run_of("p_04")}
    monkeypatch.setattr("backend.shop.shopping.run", lambda *a, **k: decision)
    seen = []
    with patch("backend.checkout.orders.create_order") as create:
        out = shop.buy("shoes", None, progress=seen.append, guard=True)
    create.assert_not_called()
    assert out["checkout"]["blocked"] and seen[-1]["kind"] == "blocked"
    assert out["receipt_id"]  # the intent was still logged first


def test_public_shop_route_turns_the_guard_on(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import mandates
    from backend.app.main import app
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id"); monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s")
    m = mandates.create("shoes", {"size": "8", "max_price": 120.0, "currency": "USD"})
    mandates.confirm(m["mandate_id"], m["confirm_token"])
    seen = {}

    def fake_buy(*a, **kw):
        seen.update(kw)
        return {"receipt_id": "r"}
    with patch("backend.app.main.shop.buy", side_effect=fake_buy):
        assert TestClient(app).post("/shop", json={"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"]}).status_code == 200
    assert seen["guard"] is True


# --- mandates: persisted, and retryable until an order may exist ---

def _confirmed():
    from backend import mandates
    m = mandates.create("shoes", {"size": "8", "max_price": 120.0, "currency": "USD"})
    mandates.confirm(m["mandate_id"], m["confirm_token"])
    return {"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"]}


def _post(monkeypatch, buy):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id"); monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    mid = _confirmed()
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", side_effect=buy):
        return client, mid, client.post("/shop", json=mid)


def test_mandates_live_in_the_database_not_in_process_memory():
    from backend import mandates
    from backend.ledger import store
    m = mandates.create("shoes", {"size": "8"})
    assert store.read("SELECT request_text FROM mandates WHERE mandate_id = ?", (m["mandate_id"],))[0][0] == "shoes"
    ledger.clear()  # a fresh database: gone, with a clear 404 reason
    with pytest.raises(mandates.MandateNotFound):
        mandates.confirm(m["mandate_id"], m["confirm_token"])
    assert ledger.verify_chain()["ok"]  # never part of the hash chain


def test_failed_before_payment_can_retry_with_the_same_mandate(monkeypatch):
    from backend.agents.shopping import ShoppingError
    calls = []

    def buy(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ShoppingError("agent hit max_tokens")
        return {"receipt_id": "r"}
    client, mid, first = _post(monkeypatch, buy)
    assert first.status_code == 422
    with patch("backend.app.main.shop.buy", side_effect=buy):
        assert client.post("/shop", json=mid).status_code == 200
        assert client.post("/shop", json=mid).status_code == 409  # a success uses it up


def test_checkout_precheck_failure_is_retryable(monkeypatch):
    def buy(*a, progress=None, **kw):
        progress({"kind": "ordering"})
        raise checkout.CheckoutError("Ledger failed verification")  # raised before any PayPal call
    client, mid, first = _post(monkeypatch, buy)
    assert first.status_code == 422
    with patch("backend.app.main.shop.buy", return_value={"receipt_id": "r"}):
        assert client.post("/shop", json=mid).status_code == 200


def test_failure_once_ordering_began_keeps_the_mandate_used(monkeypatch):
    from backend.paypal.client import PayPalError

    def buy(*a, progress=None, **kw):
        progress({"kind": "ordering"})
        raise PayPalError(500, "boom", None)  # the order may or may not exist
    client, mid, first = _post(monkeypatch, buy)
    assert first.status_code == 502
    with patch("backend.app.main.shop.buy", return_value={"receipt_id": "r"}) as again:
        assert client.post("/shop", json=mid).status_code == 409
    again.assert_not_called()


def test_blocked_purchase_is_not_a_failure_and_keeps_the_mandate_used(monkeypatch):
    client, mid, first = _post(monkeypatch, lambda *a, **kw: {"receipt_id": "r", "checkout": {"blocked": True}})
    assert first.status_code == 200
    assert client.post("/shop", json=mid).status_code == 409


# --- nothing fits: decided in code, no model, no receipt, no PayPal ---

def test_cheapest_size_8_running_shoe_message_comes_from_the_catalog():
    from backend import guard
    c = {"category": "running shoes", "size": "8", "max_price": 50.0, "currency": "USD"}
    assert guard.matching_products(c) == []
    assert guard.no_match_message(c) == ("No product matches your limits (cheapest size-8 running shoe is $64.99). "
                                         "Nothing was charged.")
    assert guard.matching_products({**c, "max_price": 120.0})
    nothing = guard.no_match_message({"category": "unicycles", "size": "8", "max_price": 50.0, "currency": "USD"})
    assert "nothing in that category" in nothing and nothing.endswith("Nothing was charged.")


def test_no_match_skips_the_model_the_ledger_and_paypal(monkeypatch):
    called = []
    monkeypatch.setattr("backend.shop.shopping.run", lambda *a, **k: called.append("model"))
    seen = []
    with patch("backend.checkout.orders.create_order") as create:
        out = shop.buy("shoes", None, progress=seen.append, guard=True,
                       constraints={"category": "running shoes", "size": "8", "max_price": 50.0, "currency": "USD"})
    create.assert_not_called()
    assert called == [] and out["no_match"] is True and "$64.99" in out["message"]
    assert seen == [{"kind": "no_match", "message": out["message"]}]
    assert ledger.verify_chain()["records"] == 0  # no intent was logged


def test_no_match_releases_the_mandate_so_the_buyer_can_adjust(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import mandates
    from backend.app.main import app
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id"); monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s")
    m = mandates.create("shoes", {"category": "running shoes", "size": "8", "max_price": 50.0, "currency": "USD"})
    mandates.confirm(m["mandate_id"], m["confirm_token"])
    client = TestClient(app)
    with patch("backend.shop.shopping.run") as model:
        first = client.post("/shop", json={"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"]})
        second = client.post("/shop", json={"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"]})
    model.assert_not_called()
    assert first.status_code == 200 and first.json()["no_match"] and second.status_code == 200  # not "already used"


def test_prompt_no_longer_asks_the_model_to_explain_why_nothing_fits():
    from backend.agents import shopping
    assert "explain why" not in shopping.SYSTEM


# --- refusals read well ---

def test_refusal_message_is_readable_and_keeps_the_details():
    from backend.agents.llm import RefusalError
    err = RefusalError("RefusalStopDetails(category='reasoning_extraction')")
    assert "RefusalStopDetails" not in str(err) and err.details.startswith("RefusalStopDetails")


def test_task_page_gets_the_readable_refusal_message(monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from backend import mandates
    from backend.agents.llm import RefusalError
    from backend.app.main import SHOP_REFUSED, app
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id"); monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s")
    m = mandates.create("shoes", {"category": "running shoes", "size": "8", "max_price": 120.0, "currency": "USD"})
    mandates.confirm(m["mandate_id"], m["confirm_token"])
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", side_effect=RefusalError("RefusalStopDetails(...)")):
        job_id = client.post("/shop", json={"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"], "background": True}).json()["job_id"]
        for _ in range(100):
            job = client.get(f"/shop/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.02)
        sync = client.post("/shop", json={"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"]})  # released after the failure: retryable
    assert job["status"] == "error" and job["error"] == SHOP_REFUSED and "RefusalStopDetails" not in job["error"]
    assert sync.status_code == 422 and sync.json()["detail"] == SHOP_REFUSED


def test_agent_prompt_keeps_the_wording_that_does_not_trigger_refusals():
    # Live checks (Nov 12, claude-sonnet-5-5): "explain why" and "a decision_summary of a few words" /
    # "a short note" / a quoted sentence were refused (reasoning_extraction) on ordinary requests,
    # "say so briefly in decision_summary" passed 4/4. Re-run a live check before rewording this sentence.
    from backend.agents import shopping
    assert "say so briefly in decision_summary" in shopping.SYSTEM
    assert "explain why" not in shopping.SYSTEM.lower()
