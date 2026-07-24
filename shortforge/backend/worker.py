"""Single background worker thread that drains the job queue."""

from __future__ import annotations

import threading
import time

from . import db
from .pipeline import orchestrator

_stop = threading.Event()
_thread: threading.Thread | None = None

_IN_PROGRESS = {"downloading", "transcribing", "analyzing", "rendering"}


def recover_interrupted() -> None:
    """Mark jobs that were mid-flight when the process died as failed."""
    for job in db.list_jobs(limit=500):
        if job["status"] in _IN_PROGRESS:
            db.update_job(job["id"], status="error", stage="Interrupted",
                          error="Server restarted while this job was running.")


def _loop() -> None:
    while not _stop.is_set():
        job = db.next_queued_job()
        if job is None:
            _stop.wait(2.0)
            continue
        orchestrator.run_job(job["id"])


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    recover_interrupted()
    _thread = threading.Thread(target=_loop, name="shortforge-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
