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
    # Demo cost guard on the routes that call Claude or place orders (/shop, scenario runs,
    # dispute resolve): runs per minute per visitor, and runs per UTC day for everyone. 0 = no limit.
    rate_limit_per_minute: int
    rate_limit_per_day: int


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def get_settings() -> Settings:
    return Settings(
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        paypal_base_url=os.environ.get("PAYPAL_BASE_URL", SANDBOX_BASE_URL).rstrip("/"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,
        # On Render, RENDER_EXTERNAL_URL is the service's public URL; no need to set APP_BASE_URL there.
        app_base_url=(os.environ.get("APP_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
                      or "http://localhost:8000").rstrip("/"),
        paypal_vault_id=os.environ.get("PAYPAL_VAULT_ID") or None,
        database_url=os.environ.get("DATABASE_URL") or "sqlite:///./agent_receipts.db",
        paypal_webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID") or None,
        dispute_auto_file=_flag("DISPUTE_AUTO_FILE"),
        filing_enabled=_flag("FILING_ENABLED"),
        rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE") or 3),
        rate_limit_per_day=int(os.environ.get("RATE_LIMIT_PER_DAY") or 150),
    )
