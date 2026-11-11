"""Mandates: a request the buyer has read, confirmed, and may spend once.

POST /mandate/parse stores the parsed request here and returns a mandate_id. POST /mandate/{id}/confirm
stamps the server's time and locks the constraints. The public POST /shop accepts only the id of a
confirmed, unused mandate and uses it up, so what the receipt records as "confirmed by buyer" was
actually confirmed through the API. In memory like the rate limiter and the jobs: one instance, reset on
restart, the most recent mandates kept.
"""
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

MAX_MANDATES = 500
_lock = threading.Lock()
_mandates: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


class MandateNotFound(LookupError):
    pass


class MandateNotConfirmed(RuntimeError):
    pass


class MandateUsed(RuntimeError):
    pass


def create(request_text: str, constraints: dict[str, Any]) -> dict[str, Any]:
    mandate = {"mandate_id": f"mnd_{uuid.uuid4().hex[:16]}", "request_text": request_text,
               "constraints": dict(constraints), "confirmed_by_buyer_at": None, "used": False}
    with _lock:
        _mandates[mandate["mandate_id"]] = mandate
        while len(_mandates) > MAX_MANDATES:
            _mandates.popitem(last=False)
        return _public(mandate)


def _get(mandate_id: str) -> dict[str, Any]:
    mandate = _mandates.get(mandate_id)
    if mandate is None:
        raise MandateNotFound(f"No such mandate: {mandate_id} (mandates are kept in memory and reset on restart)")
    return mandate


def _public(mandate: dict[str, Any]) -> dict[str, Any]:
    return {**mandate, "constraints": dict(mandate["constraints"])}


def confirm(mandate_id: str) -> dict[str, Any]:
    """Record the buyer's confirmation with the server's time. Confirming again changes nothing."""
    with _lock:
        mandate = _get(mandate_id)
        if mandate["confirmed_by_buyer_at"] is None:
            mandate["confirmed_by_buyer_at"] = datetime.now(timezone.utc).isoformat()
        return _public(mandate)


def claim(mandate_id: str) -> dict[str, Any]:
    """Use the mandate for one purchase: it must be confirmed and not used before."""
    with _lock:
        mandate = _get(mandate_id)
        if mandate["confirmed_by_buyer_at"] is None:
            raise MandateNotConfirmed("The buyer hasn't confirmed this mandate")
        if mandate["used"]:
            raise MandateUsed("This mandate has already been used for a purchase")
        mandate["used"] = True
        return _public(mandate)
