import pytest

from backend import ledger
from backend.ratelimit import limiter, parse_limiter


@pytest.fixture(autouse=True)
def memory_ledger():
    """Every test gets an empty in-memory ledger, never the real database file."""
    ledger.configure("sqlite:///:memory:")
    yield
    ledger.configure(None)


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    """Each test starts with no runs counted."""
    limiter.reset()
    parse_limiter.reset()
    yield
    limiter.reset()
    parse_limiter.reset()


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch):
    """Tests never depend on a developer's .env: switches that change behaviour start from a known
    state (rate limits off, filing off, no vault, no webhook id). Tests that need another value set it."""
    for name in ("FILING_ENABLED", "DISPUTE_AUTO_FILE", "PAYPAL_VAULT_ID", "PAYPAL_WEBHOOK_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("RATE_LIMIT_PER_DAY", "0")
