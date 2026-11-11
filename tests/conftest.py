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
