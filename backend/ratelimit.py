"""Demo cost guard for the public deployment.

/shop, scenario runs and dispute resolve call Claude, and the first two place sandbox orders,
so the hosted demo limits them: a few runs per minute per visitor, and a daily cap for everyone
(the cap is what bounds spend; a visitor id taken from X-Forwarded-For can be spoofed). When a
limit is hit the route answers 429 with a completed receipt the UI can link to instead.

State is in memory: one instance, reset on restart. That fits Render with a disk attached,
which can't scale past one instance anyway.
"""
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend import ledger
from backend.config import get_settings
from backend.ledger.staged import STAGED_DIR

WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = defaultdict(deque)
        self._day: str | None = None
        self._day_count = 0

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            self._day, self._day_count = None, 0

    def check(self, visitor: str, per_minute: int, per_day: int) -> tuple[str, int] | None:
        """Count a run. Returns None if allowed, else (which limit, seconds until it frees up)."""
        now = self.clock()
        today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self._lock:
            if today != self._day:
                self._day, self._day_count = today, 0
            if per_day and self._day_count >= per_day:
                midnight = datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp() + 86400
                return "day", int(midnight - now) + 1
            recent = self._recent[visitor]
            while recent and recent[0] <= now - WINDOW_SECONDS:
                recent.popleft()
            if per_minute and len(recent) >= per_minute:
                return "minute", int(recent[0] + WINDOW_SECONDS - now) + 1
            recent.append(now)
            self._day_count += 1
            return None


limiter = RateLimiter()
# Reading a request into constraints is a small Claude call made before every /shop run, so it has
# its own counter with a looser limit and doesn't eat the shop runs' budget.
parse_limiter = RateLimiter()
PARSE_FACTOR = 4


def visitor_of(request: Request) -> str:
    """The browser's address. Requests arrive via the Next proxy and Render's load balancer,
    so the first X-Forwarded-For entry is the visitor."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    return first or (request.client.host if request.client else "unknown")


def example_receipt() -> dict[str, str] | None:
    """A completed case to show instead: the latest decided verdict, else the staged receipt."""
    for event in reversed(ledger.events_of_kinds(("dispute_verdict",))):
        if event["verdict"].get("status") == "decided":
            return {"receipt_id": event["receipt_id"], "dispute_id": event["dispute_id"]}
    for path in sorted(STAGED_DIR.glob("rcpt_*.json")):
        if ledger.get_receipt(path.stem):
            return {"receipt_id": path.stem, "dispute_id": None}
    return None


def demo_limit(request: Request) -> None:
    """FastAPI dependency for the costly routes. Raises 429 when a limit is reached."""
    _enforce(request, limiter, 1)


def demo_limit_parse(request: Request) -> None:
    """The same limits, counted separately and PARSE_FACTOR times looser, for /mandate/parse."""
    _enforce(request, parse_limiter, PARSE_FACTOR)


def _enforce(request: Request, counter: RateLimiter, factor: int) -> None:
    settings = get_settings()
    hit = counter.check(visitor_of(request), settings.rate_limit_per_minute * factor,
                        settings.rate_limit_per_day * factor)
    if hit is None:
        return
    which, retry_after = hit
    runs = lambda n: f"{n} run{'' if n == 1 else 's'}"  # noqa: E731
    detail: dict[str, Any] = {
        "error": "demo_limit",
        "limit": which,
        "message": (f"This demo allows {runs(settings.rate_limit_per_minute)} per minute per visitor."
                    if which == "minute" else
                    f"This demo has reached its daily limit of {runs(settings.rate_limit_per_day)}."),
        "retry_after": retry_after,
        "example": example_receipt(),
    }
    raise HTTPException(429, detail, headers={"Retry-After": str(retry_after)})
