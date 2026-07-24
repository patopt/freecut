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
from . import tiktok as tt_mod
from . import tiktok_browser as tt_browser
from . import tiktok_publish as tt_publish
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


def enqueue_channel(yt_channel: dict, platform: str = "youtube") -> int:
    cfg = yt_channel.get("auto_config", {}) or {}
    lang = cfg.get("target_lang", "fr")
    music_id = cfg.get("music_id", "") or db.effective("default_dub_music") or ""
    caption_style = cfg.get("caption_style", "") or db.effective("default_dub_captions") or ""
    ytid = yt_channel["id"]

    new_dubs: list[str] = []
    for s in _candidate_shorts(cfg):
        if db.get_dub_for(s["id"], lang, ytid):
            continue  # already handled for this channel
        did = db.create_dub(s["id"], lang, dest_channel_id=ytid, music_id=music_id,
                            caption_style=caption_style)
        new_dubs.append(did)

    if not new_dubs:
        return 0

    mode = cfg.get("cadence_mode", "optimized")
    times = OPTIMIZED_TIMES if mode == "optimized" else (cfg.get("times") or OPTIMIZED_TIMES)
    jitter = JITTER_MIN if mode == "optimized" else 0
    first_run = db.last_publish_time(ytid) == 0
    start_after = max(db.last_publish_time(ytid), time.time())
    slots = compute_next_slots(len(new_dubs), times, start_after, jitter)
    # On the very first activation, publish the first clip ~2 min out so the
    # user sees it work, then follow the normal cadence for the rest.
    if first_run and slots:
        slots[0] = time.time() + 120
    for did, ts in zip(new_dubs, slots):
        db.enqueue_publish(did, ytid, ts, platform=platform)
    return len(new_dubs)


def run_auto_enqueue(refresh_sources: bool) -> None:
    targets = [(ch, "youtube") for ch in db.list_youtube_channels()]
    targets += [(acc, "tiktok") for acc in db.list_tiktok_accounts()]
    refreshed: set[str] = set()
    for target, platform in targets:
        if not target.get("auto_enabled"):
            continue
        cfg = target.get("auto_config", {}) or {}
        if refresh_sources:
            for scid in cfg.get("source_channel_ids", []) or []:
                if scid in refreshed:
                    continue
                refreshed.add(scid)
                try:
                    channels_mod.refresh_channel(scid)
                except Exception:  # noqa: BLE001
                    pass
        try:
            enqueue_channel(target, platform)
        except Exception:  # noqa: BLE001
            pass


PUBLISH_TIMEOUT = 45 * 60  # a stuck "publishing" row must not hang forever


def reap_stuck_publishes() -> None:
    """Fail rows left in 'publishing' by a crash/hang so the UI never sticks."""
    now = time.time()
    for row in db.publishes_by_status("publishing"):
        if now - float(row.get("updated_at") or 0) > PUBLISH_TIMEOUT:
            db.update_publish(
                row["id"], status="error",
                error="Publishing timed out. Use 'Publish manually' to finish it.")


def publish_due() -> None:
    reap_stuck_publishes()
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
        platform = item.get("platform") or "youtube"
        title = dub.get("tr_title") or dub.get("title") or "Short"
        desc = dub.get("tr_description") or dub.get("description") or ""
        tags = [t.strip() for t in (dub.get("tags") or "").split(",") if t.strip()]

        if platform == "tiktok":
            account = db.get_tiktok_account(item["yt_channel_id"])
            if not account:
                db.update_publish(item["id"], status="error", error="TikTok account disconnected")
                continue
            db.update_publish(item["id"], status="publishing")
            try:
                cfg = account.get("auto_config", {}) or {}
                caption = " ".join(
                    [title] + [f"#{t.replace(' ', '')}" for t in tags[:5]]).strip()
                if (account.get("mode") or "api") == "browser":
                    logs: list[str] = []
                    # Preferred: tiktok-uploader (cookie-based, fully automatic).
                    pid = tt_publish.try_post(
                        account, dub["path"], caption, log=logs.append)
                    if pid is None:
                        # Fall back to the in-house Playwright uploader.
                        logs.append("Falling back to the built-in uploader")
                        pid = tt_browser.post_video(
                            account, dub["path"], caption, log=logs.append)
                    if logs:
                        db.log_activity("publish", f"TikTok upload log: {title}",
                                        " | ".join(logs[-6:]), "info", "dub", item["dub_id"])
                else:
                    pid = tt_mod.post_video(
                        account, dub["path"], caption,
                        mode=cfg.get("post_mode", "direct"),
                        privacy=cfg.get("privacy", "public"))
                db.update_publish(item["id"], status="published", yt_video_id=pid)
                db.log_activity("publish", f"Posted to TikTok: {title}",
                                f"{account.get('display_name', '')} · {(account.get('mode') or 'api')}",
                                "success", "dub", item["dub_id"])
            except Exception as exc:  # noqa: BLE001
                db.update_publish(item["id"], status="error", error=str(exc)[:300])
                db.log_activity("publish", f"TikTok post failed: {title}",
                                str(exc)[:400], "error", "dub", item["dub_id"])
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
            privacy = (yt_channel.get("auto_config", {}) or {}).get("privacy", "public")
            vid = yt_mod.upload_video(account, dub["path"], title, desc, tags, privacy)
            db.update_publish(item["id"], status="published", yt_video_id=vid)
            db.log_activity("publish", f"Published to YouTube: {title}",
                            f"{yt_channel.get('title', '')} · https://youtu.be/{vid}",
                            "success", "dub", item["dub_id"])
        except Exception as exc:  # noqa: BLE001
            db.update_publish(item["id"], status="error", error=str(exc)[:300])
            db.log_activity("publish", f"YouTube upload failed: {title}",
                            str(exc)[:400], "error", "dub", item["dub_id"])


def _loop() -> None:
    global _last_enqueue, _last_source_refresh
    while not _stop.is_set():
        now = time.time()
        try:
            if db.is_paused():
                _stop.wait(60.0)
                continue
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
