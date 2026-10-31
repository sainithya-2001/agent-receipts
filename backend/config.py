"""Settings loaded from the environment (.env in local dev). No secrets live in code."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str
    anthropic_model: str | None
    app_base_url: str
    # Saved buyer wallet (scripts/vault.py). When set, orders charge it with no redirect.
    paypal_vault_id: str | None
    # Ledger database. Relative sqlite paths resolve against the repo root.
    database_url: str
    # Webhook id from the PayPal developer dashboard; needed to verify webhook signatures.
    paypal_webhook_id: str | None
    # When true, dispute webhooks file responses with PayPal (accept claim, evidence, ...).
    # Off by default so a webhook can't settle a pre-staged demo dispute by accident.
    dispute_auto_file: bool
    # Master switch for every PayPal filing (route, webhook, CLI). Off by default; turned on
    # locally only for the recording-prep sitting. DISPUTE_AUTO_FILE does nothing without it.
    filing_enabled: bool


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def get_settings() -> Settings:
    return Settings(
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        paypal_base_url=os.environ.get("PAYPAL_BASE_URL", SANDBOX_BASE_URL).rstrip("/"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
        paypal_vault_id=os.environ.get("PAYPAL_VAULT_ID") or None,
        database_url=os.environ.get("DATABASE_URL") or "sqlite:///./agent_receipts.db",
        paypal_webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID") or None,
        dispute_auto_file=_flag("DISPUTE_AUTO_FILE"),
        filing_enabled=_flag("FILING_ENABLED"),
    )
