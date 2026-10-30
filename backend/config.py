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


def get_settings() -> Settings:
    return Settings(
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        paypal_base_url=os.environ.get("PAYPAL_BASE_URL", SANDBOX_BASE_URL).rstrip("/"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,
    )
