"""Background worker pool draining the clip-job and dub queues.

Sized for a small VPS (the reference box is 4 vCPU / 4 GB): several pipelines
run at once, but the memory-heavy and CPU-heavy stages are throttled by
semaphores so they can't thrash the machine. Jobs are claimed atomically in
SQLite, so two workers never pick up the same item.
"""

from __future__ import annotations

import os
import threading

from . import db
from .pipeline import dub, orchestrator

_stop = threading.Event()
_threads: list[threading.Thread] = []

_IN_PROGRESS = {"downloading", "transcribing", "analyzing", "rendering"}
_DUB_IN_PROGRESS = {"downloading", "transcribing", "translating", "dubbing", "rendering"}


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 2
    except Exception:  # noqa: BLE001
        return 2


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


# How many full pipelines run at once. Whisper dominates memory (~700 MB per
# instance in int8), so 4 GB comfortably holds two.
PIPELINE_WORKERS = _env_int("PIPELINE_WORKERS", min(2, max(1, _cpu_count() // 2)), 1, 8)


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
        # Atomic claims keep the workers from racing over the same item.
        job = db.claim_next_job()
        if job is not None:
            orchestrator.run_job(job["id"])
            continue
        d = db.claim_next_dub()
        if d is not None:
            dub.run_dub(d["id"])
            continue
        _stop.wait(2.0)


def start_worker() -> None:
    if _threads:
        return
    recover_interrupted()
    for i in range(PIPELINE_WORKERS):
        t = threading.Thread(target=_loop, name=f"shortforge-worker-{i + 1}", daemon=True)
        t.start()
        _threads.append(t)


def stop_worker() -> None:
    _stop.set()
