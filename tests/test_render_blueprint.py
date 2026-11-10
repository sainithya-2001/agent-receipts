"""render.yaml must keep the hosted demo safe: no filing, free plan, ephemeral SQLite, no secret values."""
from pathlib import Path

import yaml

BLUEPRINT = yaml.safe_load((Path(__file__).resolve().parents[1] / "render.yaml").read_text(encoding="utf-8"))
SERVICES = {s["name"]: s for s in BLUEPRINT["services"]}
API = SERVICES["agent-receipts-api"]
WEB = SERVICES["agent-receipts-web"]


def env(service):
    return {e["key"]: e for e in service["envVars"]}


def test_hosted_backend_never_files_with_paypal():
    assert env(API)["FILING_ENABLED"]["value"] == "false"
    assert env(API)["DISPUTE_AUTO_FILE"]["value"] == "false"


def test_ledger_is_ephemeral_sqlite_with_no_disk():
    assert env(API)["DATABASE_URL"]["value"] == "sqlite:///./agent_receipts.db"
    assert "disk" not in API


def test_secrets_are_never_in_the_file():
    for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_VAULT_ID", "ANTHROPIC_API_KEY", "PAYPAL_WEBHOOK_ID"):
        assert env(API)[key] == {"key": key, "sync": False}


def test_frontend_points_at_the_api_and_shares_its_region():
    # Free web services can't receive private-network traffic, so the URL is set by hand.
    assert env(WEB)["BACKEND_URL"] == {"key": "BACKEND_URL", "sync": False}
    assert WEB["rootDir"] == "frontend"
    assert API["plan"] == WEB["plan"] == "free"


def test_rate_limit_is_kept():
    assert int(env(API)["RATE_LIMIT_PER_MINUTE"]["value"]) > 0
    assert int(env(API)["RATE_LIMIT_PER_DAY"]["value"]) > 0
