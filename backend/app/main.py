"""FastAPI entry point."""
import logging

from fastapi import FastAPI

from backend.config import get_settings
from backend.paypal.client import PayPalClient, PayPalError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Agent Receipts")


@app.get("/health")
def health(deep: bool = False) -> dict:
    """Liveness check. With ?deep=true, also confirms PayPal credentials work."""
    settings = get_settings()
    result: dict = {"status": "ok", "paypal_base_url": settings.paypal_base_url}
    if deep:
        try:
            PayPalClient().access_token()
            result["paypal_auth"] = "ok"
        except (ValueError, PayPalError) as exc:
            result["status"] = "degraded"
            result["paypal_auth"] = str(exc)
    return result
