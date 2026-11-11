"""Background shop runs with progress, so the task page can show what the agent is doing.

A run is a function that takes a progress callback. Events are appended as it goes and the
browser polls GET /shop/jobs/{id}. In memory, like the rate limiter: one instance, reset on
restart; only the most recent runs are kept.
"""
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

MAX_JOBS = 50
_lock = threading.Lock()
_jobs: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def start(run: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]],
          describe_error: Callable[[Exception], str]) -> str:
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {"job_id": job_id, "status": "running", "events": [], "result": None, "error": None}
    with _lock:
        _jobs[job_id] = job
        while len(_jobs) > MAX_JOBS:
            _jobs.popitem(last=False)

    def progress(event: dict[str, Any]) -> None:
        with _lock:
            job["events"].append({"at": time.time(), **event})

    def work() -> None:
        try:
            result = run(progress)
            with _lock:
                job["result"], job["status"] = result, "done"
        except Exception as exc:  # reported to the page, never raised into the server
            with _lock:
                job["error"], job["status"] = describe_error(exc), "error"

    threading.Thread(target=work, daemon=True, name=f"shop-{job_id}").start()
    return job_id


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return None if job is None else {**job, "events": list(job["events"])}
