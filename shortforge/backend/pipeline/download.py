"""Download the source video with yt-dlp (or accept a local file path)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

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
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 3,
        "concurrent_fragment_downloads": 4,
    }

    # Datacenter/VPS IPs are often gated behind a login. If a Netscape
    # cookies.txt is present (captured by the remote browser), use it — this is
    # the reliable fix for "sign in to confirm you're not a bot".
    cookies = config.DATA_DIR / "cookies.txt"
    has_cookies = cookies.exists()
    if has_cookies:
        ydl_opts["cookiefile"] = str(cookies)
    else:
        # Without cookies, forcing several player clients works around
        # "not available on this app". These extra clients must NOT be used
        # together with cookies: android/ios ignore them and YouTube then
        # returns no formats at all ("Requested format is not available").
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["default", "tv", "web_safari", "android", "ios"]}
        }

    # Format cascade, most-preferred first. YouTube often only offers VP9/AV1
    # (webm) video or Opus audio for a given resolution, so never pin the
    # container; the last entry lets yt-dlp pick whatever exists.
    format_candidates = [
        "bestvideo*[height<=1920]+bestaudio/best[height<=1920]",
        "bestvideo*+bestaudio/best",
        "best",
        None,  # yt-dlp's own default
    ]

    def _attempt(base_opts: dict) -> tuple[Optional[dict], Optional[Exception]]:
        last: Optional[Exception] = None
        for fmt in format_candidates:
            opts = dict(base_opts)
            if fmt:
                opts["format"] = fmt
            else:
                opts.pop("format", None)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=True), None
            except Exception as exc:  # noqa: BLE001
                last = exc
                message = str(exc).lower()
                # Only a format problem is worth retrying; anything else
                # (private video, bot check, network) fails identically.
                if "requested format" not in message and "format is not available" not in message:
                    raise
                for stale in work.glob("source.*"):
                    stale.unlink(missing_ok=True)
        return None, last

    info, last_error = _attempt(ydl_opts)

    # Last resort: expired/invalid cookies can make YouTube expose no formats at
    # all. Retry once cookie-less with the multi-client workaround.
    if info is None and has_cookies:
        on_progress(0.0, "Retrying without cookies…")
        fallback = {k: v for k, v in ydl_opts.items() if k != "cookiefile"}
        fallback["extractor_args"] = {
            "youtube": {"player_client": ["default", "tv", "web_safari", "android", "ios"]}
        }
        info, last_error = _attempt(fallback)

    if info is None:
        raise RuntimeError(
            f"Download failed: {last_error}. If this keeps happening, refresh the "
            f"YouTube cookies from Settings (remote browser) or run "
            f"'pip install -U yt-dlp'.")

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
