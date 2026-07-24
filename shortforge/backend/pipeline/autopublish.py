"""Auto-publish scheduler: turns connected YouTube channels into a factory.

A single daemon thread:
  1. publishes due queue items (once their dub is rendered), and
  2. periodically enqueues new dubs from the configured source channels at a
     healthy cadence, refreshing sources to pick up newly-uploaded videos.

Cadence research (2026): 1-2 Shorts/day is the growth sweet spot, best window
12h-15h. The "optimized" preset posts 2/day around then, with per-slot jitter
so it never looks robotic.
"""

from __future__ import annotations

import datetime as dt
import random
import threading
import time

from .. import db
from . import channels as channels_mod
from . import youtube as yt_mod

# Optimized preset: 2 posts/day inside the 12h-15h window (server local time).
OPTIMIZED_TIMES = ["12:20", "14:10"]
JITTER_MIN = 20

_stop = threading.Event()
_thread: threading.Thread | None = None
_last_enqueue = 0.0
_last_source_refresh = 0.0
ENQUEUE_EVERY = 30 * 60          # re-scan config -> queue every 30 min
SOURCE_REFRESH_EVERY = 6 * 3600  # re-scan source channels for new videos every 6h


def compute_next_slots(n: int, times_hhmm: list[str], start_after: float, jitter_min: int) -> list[float]:
    parsed = []
    for t in times_hhmm:
        try:
            h, m = t.split(":")
            parsed.append((int(h), int(m)))
        except ValueError:
            continue
    parsed.sort()
    if not parsed:
        parsed = [(12, 20), (14, 10)]
    slots: list[float] = []
    base_day = dt.datetime.fromtimestamp(start_after).replace(hour=0, minute=0, second=0, microsecond=0)
    day = 0
    while len(slots) < n and day < 3650:
        d0 = base_day + dt.timedelta(days=day)
        for (h, m) in parsed:
            ts = (d0.replace(hour=h, minute=m)).timestamp() + random.uniform(-jitter_min, jitter_min) * 60
            if ts > start_after:
                slots.append(ts)
                if len(slots) >= n:
                    break
        day += 1
    return slots


def _candidate_shorts(cfg: dict) -> list[dict]:
    sources = cfg.get("source_channel_ids", []) or []
    cands: list[dict] = []
    for scid in sources:
        cands.extend(db.list_channel_shorts(scid))
    # Oldest first, so a backlog publishes in chronological order.
    cands.sort(key=lambda s: s.get("created_at", 0))
    selection = cfg.get("selection", "all")
    if selection == "manual":
        wanted = set(cfg.get("selected_short_ids", []) or [])
        cands = [s for s in cands if s["id"] in wanted]
    elif selection == "number":
        cands = cands[: int(cfg.get("count", 30))]
    return cands


def enqueue_channel(yt_channel: dict) -> int:
    cfg = yt_channel.get("auto_config", {}) or {}
    lang = cfg.get("target_lang", "fr")
    music_id = cfg.get("music_id", "") or db.effective("default_dub_music") or ""
    ytid = yt_channel["id"]

    new_dubs: list[str] = []
    for s in _candidate_shorts(cfg):
        if db.get_dub_for(s["id"], lang, ytid):
            continue  # already handled for this channel
        did = db.create_dub(s["id"], lang, dest_channel_id=ytid, music_id=music_id)
        new_dubs.append(did)

    if not new_dubs:
        return 0

    mode = cfg.get("cadence_mode", "optimized")
    times = OPTIMIZED_TIMES if mode == "optimized" else (cfg.get("times") or OPTIMIZED_TIMES)
    jitter = JITTER_MIN if mode == "optimized" else 0
    start_after = max(db.last_publish_time(ytid), time.time())
    slots = compute_next_slots(len(new_dubs), times, start_after, jitter)
    for did, ts in zip(new_dubs, slots):
        db.enqueue_publish(did, ytid, ts)
    return len(new_dubs)


def run_auto_enqueue(refresh_sources: bool) -> None:
    for yt_channel in db.list_youtube_channels():
        if not yt_channel.get("auto_enabled"):
            continue
        cfg = yt_channel.get("auto_config", {}) or {}
        if refresh_sources:
            for scid in cfg.get("source_channel_ids", []) or []:
                try:
                    channels_mod.refresh_channel(scid)
                except Exception:  # noqa: BLE001
                    pass
        try:
            enqueue_channel(yt_channel)
        except Exception:  # noqa: BLE001
            pass


def publish_due() -> None:
    for item in db.due_publishes(time.time()):
        dub = db.get_dub(item["dub_id"])
        if not dub:
            db.update_publish(item["id"], status="error", error="Dub missing")
            continue
        if dub["status"] != "done" or not dub.get("path"):
            # Not rendered yet — leave pending, try again next tick.
            if dub["status"] == "error":
                db.update_publish(item["id"], status="error", error="Dub failed")
            continue
        yt_channel = db.get_youtube_channel(item["yt_channel_id"])
        if not yt_channel:
            db.update_publish(item["id"], status="error", error="Channel gone")
            continue
        account = db.get_google_account(yt_channel["account_id"])
        if not account:
            db.update_publish(item["id"], status="error", error="Account disconnected")
            continue
        db.update_publish(item["id"], status="publishing")
        try:
            title = dub.get("tr_title") or dub.get("title") or "Short"
            desc = dub.get("tr_description") or dub.get("description") or ""
            tags = [t.strip() for t in (dub.get("tags") or "").split(",") if t.strip()]
            privacy = (yt_channel.get("auto_config", {}) or {}).get("privacy", "public")
            vid = yt_mod.upload_video(account, dub["path"], title, desc, tags, privacy)
            db.update_publish(item["id"], status="published", yt_video_id=vid)
        except Exception as exc:  # noqa: BLE001
            db.update_publish(item["id"], status="error", error=str(exc)[:300])


def _loop() -> None:
    global _last_enqueue, _last_source_refresh
    while not _stop.is_set():
        now = time.time()
        try:
            publish_due()
            if now - _last_enqueue >= ENQUEUE_EVERY:
                refresh = now - _last_source_refresh >= SOURCE_REFRESH_EVERY
                run_auto_enqueue(refresh_sources=refresh)
                _last_enqueue = now
                if refresh:
                    _last_source_refresh = now
        except Exception:  # noqa: BLE001
            pass
        _stop.wait(60.0)


def start_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="shortforge-autopublish", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()
