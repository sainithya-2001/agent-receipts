"""render.yaml must keep the hosted demo safe: no filing, ledger on the disk, no secret values."""
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


def test_ledger_database_lives_on_the_disk():
    url = env(API)["DATABASE_URL"]["value"]
    assert url.startswith("sqlite:////") and url[len("sqlite:///"):].startswith(API["disk"]["mountPath"] + "/")


def test_secrets_are_never_in_the_file():
    for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_VAULT_ID", "ANTHROPIC_API_KEY", "PAYPAL_WEBHOOK_ID"):
        assert env(API)[key] == {"key": key, "sync": False}


def test_frontend_points_at_the_api_and_shares_its_region():
    assert env(WEB)["BACKEND_URL"]["fromService"]["name"] == API["name"]
    assert WEB["region"] == API["region"] and WEB["rootDir"] == "frontend"
    assert API["plan"] == WEB["plan"] == "starter"
