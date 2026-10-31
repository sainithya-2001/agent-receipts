import pytest

from backend import ledger


@pytest.fixture(autouse=True)
def memory_ledger():
    """Every test gets an empty in-memory ledger, never the real database file."""
    ledger.configure("sqlite:///:memory:")
    yield
    ledger.configure(None)
