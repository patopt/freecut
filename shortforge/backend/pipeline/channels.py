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


def fetch_channel_shorts(url: str, limit: int = 60) -> dict:
    """Return {name, thumb, shorts:[{video_id,title,url,duration,thumb,published}]}."""
    import yt_dlp

    target = normalize_channel_url(url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": limit,
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


def refresh_channel(channel_id: str) -> None:
    """Fetch (or re-fetch) a channel's shorts into the DB. Meant to run in a thread."""
    from .. import db

    ch = db.get_channel(channel_id)
    if not ch:
        return
    db.update_channel(channel_id, status="fetching", message="Fetching shorts…")
    try:
        data = fetch_channel_shorts(ch["url"])
        new_count = 0
        for s in data["shorts"]:
            _, is_new = db.upsert_channel_short(
                channel_id, s["video_id"], title=s["title"], url=s["url"],
                thumb=s["thumb"], duration=s["duration"], published=s["published"],
            )
            new_count += 1 if is_new else 0
        db.update_channel(
            channel_id, status="ready",
            name=data["name"] or ch.get("name") or "Channel",
            thumb=data["thumb"] or ch.get("thumb") or "",
            message=f"{len(data['shorts'])} shorts ({new_count} new)",
        )
    except Exception as exc:  # noqa: BLE001
        db.update_channel(channel_id, status="error", message=str(exc)[:200])
