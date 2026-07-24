"""Download the source video with yt-dlp (or accept a local file path)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .. import config

ProgressCb = Callable[[float, str], None]


def download_source(url: str, job_id: str, on_progress: ProgressCb) -> dict:
    """Return {path, title, duration}. `url` may be a YouTube URL or a local path."""
    work = config.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)

    # Allow driving the pipeline from a file already on disk.
    if os.path.exists(url):
        src = Path(url)
        on_progress(1.0, "Local file")
        return {"path": str(src), "title": src.stem, "duration": _probe_duration(src),
                "description": "", "tags": []}

    import yt_dlp

    outtmpl = str(work / "source.%(ext)s")

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0.0
            on_progress(frac, f"Downloading {int(frac * 100)}%")
        elif d.get("status") == "finished":
            on_progress(1.0, "Download finished, muxing…")

    ydl_opts = {
        # height<=1920 so vertical shorts (1080x1920) aren't excluded.
        "format": "bestvideo[height<=1920][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 3,
        "concurrent_fragment_downloads": 4,
        # YouTube frequently rejects a single player client ("not available on
        # this app"). Try several so one succeeds.
        "extractor_args": {
            "youtube": {"player_client": ["default", "tv", "web_safari", "android", "ios"]}
        },
    }

    # Datacenter/VPS IPs are often gated behind a login. If the user drops a
    # Netscape-format cookies.txt (exported from a logged-in browser) into the
    # data dir, use it — this is the reliable fix for "sign in to confirm".
    cookies = config.DATA_DIR / "cookies.txt"
    if cookies.exists():
        ydl_opts["cookiefile"] = str(cookies)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Resolve the final merged file.
    path = work / "source.mp4"
    if not path.exists():
        # Fall back to whatever extension yt-dlp produced.
        candidates = sorted(work.glob("source.*"))
        if not candidates:
            raise RuntimeError("Download produced no file")
        path = candidates[0]

    tags = info.get("tags") or info.get("categories") or []
    return {
        "path": str(path),
        "title": info.get("title") or path.stem,
        "duration": float(info.get("duration") or _probe_duration(path)),
        "description": info.get("description") or "",
        "tags": [str(t) for t in tags] if isinstance(tags, list) else [],
    }


def _probe_duration(path: Path) -> float:
    import subprocess

    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0
