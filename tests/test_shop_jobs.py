"""POST /shop with background=true: progress events, result, errors, rate limit."""
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import jobs, mandates, shop
from backend.agents.shopping import ShoppingError
from backend.app.main import app


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("RATE_LIMIT_PER_DAY", "50")


def wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/shop/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def confirmed_mandate(request_text="shoes"):
    """A mandate that went through parse -> confirm, as the task page does."""
    m = mandates.create(request_text, {"category": "running shoes", "size": "8", "max_price": 120.0, "currency": "USD"})
    return mandates.confirm(m["mandate_id"])["mandate_id"]


def fake_buy(request_text, paypal, client=None, progress=None, constraints=None, confirmed_at=None, confirmation='cli'):
    progress({"kind": "step", "step": {"tool": "search_products", "result_ids": ["p_01"]}})
    progress({"kind": "logged", "receipt_id": "rcpt_x", "constraints": {}, "chosen_product_id": "p_01"})
    progress({"kind": "ordering"})
    return {"receipt_id": "rcpt_x", "checkout": {"status": "COMPLETED"}}


def test_background_run_reports_progress_then_result():
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", side_effect=fake_buy):
        started = client.post("/shop", json={"mandate_id": confirmed_mandate(), "background": True})
        job = wait(client, started.json()["job_id"])
    assert job["status"] == "done" and job["result"]["receipt_id"] == "rcpt_x"
    assert [e["kind"] for e in job["events"]] == ["step", "logged", "ordering"]


def test_background_error_is_reported_not_raised():
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", side_effect=ShoppingError("Agent hit max_tokens")):
        job = wait(client, client.post("/shop", json={"mandate_id": confirmed_mandate(), "background": True}).json()["job_id"])
    assert job["status"] == "error" and "max_tokens" in job["error"]


def test_unknown_job_is_404():
    assert TestClient(app).get("/shop/jobs/nope").status_code == 404


def test_rate_limit_applies_to_background_runs():
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", side_effect=fake_buy):
        codes = [client.post("/shop", json={"mandate_id": confirmed_mandate(), "background": True}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_buy_emits_events_in_order(monkeypatch):
    decision = {"mandate": {"request_text": "r", "constraints": {"size": "8", "max_price": 120, "currency": "USD"}},
                "agent_run": {"model": "m", "options_considered": [], "chosen_product_id": "p_03", "chosen_size": "8",
                              "reasoning": "r", "steps": []}}

    def run(request_text, client=None, on_step=None, constraints=None, confirmed_at=None):
        on_step({"tool": "get_product", "result_ids": ["p_03"]})
        return decision

    monkeypatch.setattr(shop.shopping, "run", run)
    monkeypatch.setattr(shop.ledger, "log_intent", lambda mandate, run: "rcpt_1")
    monkeypatch.setattr(shop.checkout, "start", lambda paypal, rid: {"status": "COMPLETED"})
    seen = []
    shop.buy("r", paypal=None, progress=seen.append)
    assert [e["kind"] for e in seen] == ["step", "logged", "ordering", "ordered"]
    assert seen[1]["receipt_id"] == "rcpt_1"


def test_job_store_keeps_only_recent_runs():
    ids = [jobs.start(lambda progress: {}, str) for _ in range(jobs.MAX_JOBS + 3)]
    assert jobs.get(ids[0]) is None and jobs.get(ids[-1]) is not None
