"""Single background worker thread that drains the job queue."""

from __future__ import annotations

import threading
import time

from . import db
from .pipeline import dub, orchestrator

_stop = threading.Event()
_thread: threading.Thread | None = None

_IN_PROGRESS = {"downloading", "transcribing", "analyzing", "rendering"}
_DUB_IN_PROGRESS = {"downloading", "transcribing", "translating", "dubbing", "rendering"}


def recover_interrupted() -> None:
    """Mark jobs/dubs that were mid-flight when the process died as failed."""
    for job in db.list_jobs(limit=500):
        if job["status"] in _IN_PROGRESS:
            db.update_job(job["id"], status="error", stage="Interrupted",
                          error="Server restarted while this job was running.")
    for d in db.list_all_dubs_in_progress():
        if d["status"] in _DUB_IN_PROGRESS:
            db.update_dub(d["id"], status="error", stage="Interrupted",
                          error="Server restarted while this dub was running.")


def _loop() -> None:
    while not _stop.is_set():
        if db.is_paused():
            _stop.wait(2.0)
            continue
        job = db.next_queued_job()
        if job is not None:
            orchestrator.run_job(job["id"])
            continue
        d = db.next_queued_dub()
        if d is not None:
            dub.run_dub(d["id"])
            continue
        _stop.wait(2.0)


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    recover_interrupted()
    _thread = threading.Thread(target=_loop, name="shortforge-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
