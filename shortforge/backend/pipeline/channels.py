"""Enumerate a YouTube channel's shorts with yt-dlp (flat, no download)."""

from __future__ import annotations

import re

from .. import config


def normalize_channel_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    if "/shorts" not in url and "youtube.com" in url:
        url = url + "/shorts"
    return url


def _thumb_for(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def fetch_channel_shorts(url: str, start: int = 1, end: int = 50) -> dict:
    """Fetch a slice [start..end] (1-based) of a channel's shorts.

    Returns {name, thumb, shorts:[…]}. Fetching in small slices avoids one huge
    blocking extraction and stays gentle on rate limits.
    """
    import yt_dlp

    target = normalize_channel_url(url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playliststart": start,
        "playlistend": end,
        "extractor_args": {
            "youtube": {"player_client": ["default", "tv", "web_safari"]}
        },
    }
    cookies = config.DATA_DIR / "cookies.txt"
    if cookies.exists():
        ydl_opts["cookiefile"] = str(cookies)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)

    entries = info.get("entries") or []
    name = info.get("channel") or info.get("uploader") or info.get("title") or "Channel"

    shorts = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id")
        if not vid or not re.fullmatch(r"[\w-]{6,}", vid):
            continue
        shorts.append({
            "video_id": vid,
            "title": e.get("title") or "Short",
            "url": e.get("url") or f"https://www.youtube.com/shorts/{vid}",
            "duration": float(e.get("duration") or 0),
            "thumb": _thumb_for(vid),
            "published": str(e.get("upload_date") or ""),
        })

    channel_thumb = shorts[0]["thumb"] if shorts else ""
    return {"name": name, "thumb": channel_thumb, "shorts": shorts}


CHUNK = 50
MAX_SHORTS = 1000


def refresh_channel(channel_id: str) -> None:
    """Fetch a channel's shorts into the DB in small slices (background thread)."""
    from .. import db

    ch = db.get_channel(channel_id)
    if not ch:
        return
    db.update_channel(channel_id, status="fetching", message="Fetching shorts…")
    try:
        total = 0
        new_count = 0
        name = ch.get("name") or "Channel"
        thumb = ch.get("thumb") or ""
        start = 1
        while start <= MAX_SHORTS:
            data = fetch_channel_shorts(ch["url"], start=start, end=start + CHUNK - 1)
            batch = data["shorts"]
            name = data["name"] or name
            thumb = thumb or data["thumb"]
            for s in batch:
                _, is_new = db.upsert_channel_short(
                    channel_id, s["video_id"], title=s["title"], url=s["url"],
                    thumb=s["thumb"], duration=s["duration"], published=s["published"],
                )
                new_count += 1 if is_new else 0
            total += len(batch)
            # Progressive UI update after each slice.
            db.update_channel(channel_id, status="fetching", name=name, thumb=thumb,
                              message=f"Fetched {total} shorts…")
            if len(batch) < CHUNK:
                break  # reached the end of the channel
            start += CHUNK
        db.update_channel(channel_id, status="ready", name=name, thumb=thumb,
                          message=f"{total} shorts ({new_count} new)")
    except Exception as exc:  # noqa: BLE001
        db.update_channel(channel_id, status="error", message=str(exc)[:200])
