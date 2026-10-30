"""Thin PayPal REST client: OAuth token cache + one logged request helper.

Every PayPal call in the project goes through PayPalClient.request so that the
PayPal-Debug-Id and status of each call end up in the logs.
"""
import logging
import time
import uuid
from typing import Any

import httpx

from backend.config import get_settings

log = logging.getLogger("paypal")

# Refresh the token this many seconds before PayPal says it expires.
TOKEN_SKEW_SECONDS = 60


class PayPalError(Exception):
    def __init__(self, status: int, body: Any, debug_id: str | None):
        self.status = status
        self.body = body
        self.debug_id = debug_id
        name = body.get("name") if isinstance(body, dict) else None
        super().__init__(f"PayPal {status} {name or ''} (debug_id={debug_id})")


class PayPalClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        http: httpx.Client | None = None,
    ):
        settings = get_settings()
        self.client_id = client_id or settings.paypal_client_id
        self.client_secret = client_secret or settings.paypal_client_secret
        self.base_url = (base_url or settings.paypal_base_url).rstrip("/")
        if not self.client_id or not self.client_secret:
            raise ValueError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set")
        self.http = http or httpx.Client(timeout=30.0)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        resp = self.http.post(
            f"{self.base_url}/v1/oauth2/token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        debug_id = resp.headers.get("paypal-debug-id")
        log.info("POST /v1/oauth2/token -> %s debug_id=%s", resp.status_code, debug_id)
        if resp.status_code != 200:
            raise PayPalError(resp.status_code, _json_or_text(resp), debug_id)
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 0)) - TOKEN_SKEW_SECONDS
        return self._token

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        files: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        """Call PayPal and return the parsed JSON body (None for empty bodies).

        Pass `files` for multipart endpoints (e.g. provide-evidence); httpx then
        sets the multipart Content-Type itself.
        """
        hdrs = {"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"}
        if files is None:
            hdrs["Content-Type"] = "application/json"
        if method.upper() == "POST":
            # Makes retries of the same call safe on PayPal's side.
            hdrs["PayPal-Request-Id"] = str(uuid.uuid4())
        hdrs.update(headers or {})
        resp = self.http.request(
            method, f"{self.base_url}{path}", json=json, params=params, files=files, headers=hdrs
        )
        debug_id = resp.headers.get("paypal-debug-id")
        log.info("%s %s -> %s debug_id=%s", method.upper(), path, resp.status_code, debug_id)
        if resp.status_code >= 400:
            raise PayPalError(resp.status_code, _json_or_text(resp), debug_id)
        return _json_or_text(resp) if resp.content else None


def _json_or_text(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text
