"""Demo cost guard: per-visitor runs per minute, a daily cap for everyone, 429 with an example."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import ledger, mandates, resolve, scenarios
from backend.app.main import app
from backend.ratelimit import RateLimiter, example_receipt, limiter
from tests.test_disputes_flow import fake_claude, real_dispute

VISITOR_A = {"X-Forwarded-For": "203.0.113.1, 10.0.0.5"}
VISITOR_B = {"X-Forwarded-For": "198.51.100.7"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("RATE_LIMIT_PER_DAY", "5")
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)  # scenario runs stay offline
    monkeypatch.delenv("PAYPAL_VAULT_ID", raising=False)
    # These tests are about the flow, not the anchor check (see test_fail_closed.py for that).
    monkeypatch.setattr(resolve, "_anchor", lambda *args, **kwargs: None)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_per_visitor_window_slides():
    clock = Clock()
    rl = RateLimiter(clock)
    assert [rl.check("a", 3, 0) for _ in range(3)] == [None, None, None]
    assert rl.check("a", 3, 0)[0] == "minute"
    assert rl.check("b", 3, 0) is None  # another visitor is unaffected
    clock.t += 61
    assert rl.check("a", 3, 0) is None


def test_daily_cap_is_shared_and_resets_at_utc_midnight():
    clock = Clock(1_800_000_000.0)  # 2027-01-15 08:00 UTC
    rl = RateLimiter(clock)
    for visitor in "abcde":
        assert rl.check(visitor, 3, 5) is None
    which, retry = rl.check("f", 3, 5)
    assert which == "day" and 0 < retry <= 86400
    clock.t += retry
    assert rl.check("f", 3, 5) is None


def test_zero_means_no_limit():
    rl = RateLimiter(Clock())
    assert all(rl.check("a", 0, 0) is None for _ in range(50))


def run(headers, name="buyer_remorse"):
    with patch("backend.agents.adjudicator.anthropic.Anthropic", return_value=fake_claude()):
        return TestClient(app).post(f"/scenarios/{name}/run", headers=headers)


def test_scenario_route_returns_429_with_example_receipt():
    for _ in range(3):
        assert run(VISITOR_A).status_code == 200
    r = run(VISITOR_A)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    detail = r.json()["detail"]
    assert detail["error"] == "demo_limit" and detail["limit"] == "minute"
    assert ledger.get_receipt(detail["example"]["receipt_id"]) is not None
    assert detail["example"]["dispute_id"].startswith("SIM-buyer_remorse")
    assert run(VISITOR_B).status_code == 200  # a different visitor still gets through


def test_daily_cap_applies_across_routes_and_visitors(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")  # /shop builds a client before buying (stubbed below)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    rid = scenarios.log_receipt(scenarios.get_scenario("wrong_size"))
    resolve.resolve(real_dispute(rid), client=fake_claude("AGENT_ERROR", "AL-2"))
    client = TestClient(app)
    with patch("backend.app.main.shop.buy", return_value={"ok": True}):
        for i in range(5):  # 5 runs from 5 visitors hit the daily cap of 5
            r = client.post("/shop", json=_confirmed_body(), headers={"X-Forwarded-For": f"192.0.2.{i}"})
            assert r.status_code == 200
    r = client.post("/disputes/PP-D-1/resolve", headers={"X-Forwarded-For": "192.0.2.99"})
    assert r.status_code == 429 and r.json()["detail"]["limit"] == "day"
    assert r.json()["detail"]["example"] == {"receipt_id": rid, "dispute_id": "PP-D-1"}


def test_example_falls_back_to_staged_receipt():
    assert example_receipt() is None
    ledger.load_staged()
    assert example_receipt() == {"receipt_id": "rcpt_627e333bfb7045c1", "dispute_id": None}


def test_read_routes_are_not_limited():
    client = TestClient(app)
    for _ in range(10):
        assert client.get("/ledger/verify", headers=VISITOR_A).status_code == 200
    assert limiter.check("203.0.113.1", 3, 5) is None


def _confirmed_body():
    m = mandates.create("x", {"currency": "USD"})
    mandates.confirm(m["mandate_id"], m["confirm_token"])
    return {"mandate_id": m["mandate_id"], "confirm_token": m["confirm_token"]}
