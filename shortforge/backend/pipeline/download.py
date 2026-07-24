"""Download the source video with yt-dlp (or accept a local file path)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from .. import config, db
from . import vpn

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

    # The original, reliable setup: force several player clients so YouTube
    # can't reject us with "not available on this app".
    MULTI_CLIENT = {
        "youtube": {"player_client": ["default", "tv", "web_safari", "android", "ios"]}
    }
    ydl_opts["extractor_args"] = MULTI_CLIENT

    # Cookies are OPT-IN (Settings): android/ios ignore them, and forcing both
    # together made YouTube expose no formats at all. Blocks are handled by VPN
    # rotation instead, which is what actually fixes datacenter-IP gating.
    cookies = config.DATA_DIR / "cookies.txt"
    has_cookies = cookies.exists() and db.use_youtube_cookies()

    # Format cascade, most-preferred first. YouTube often only offers VP9/AV1
    # (webm) video or Opus audio for a given resolution, so never pin the
    # container; the last entry lets yt-dlp pick whatever exists.
    format_candidates = [
        "bestvideo*[height<=1920]+bestaudio/best[height<=1920]",
        "bestvideo*+bestaudio/best",
        "best",
        None,  # yt-dlp's own default
    ]

    BLOCK_HINTS = (
        "requested format", "format is not available", "sign in to confirm",
        "not available on this app", "bot", "429", "too many requests",
        "unable to download", "failed to extract",
    )

    def _cleanup() -> None:
        for stale in work.glob("source.*"):
            stale.unlink(missing_ok=True)

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
                # Only YouTube-side blocks are worth retrying; a private video
                # or a dead link fails identically every time.
                if not any(hint in message for hint in BLOCK_HINTS):
                    raise
                _cleanup()
        return None, last

    # Strategies, in order. Cookies (when enabled) first, then the plain
    # multi-client setup that has always worked.
    strategies: list[tuple[str, dict]] = []
    if has_cookies:
        strategies.append(("with cookies", {**ydl_opts, "cookiefile": str(cookies)}))
    strategies.append(("standard", dict(ydl_opts)))

    info = None
    last_error: Optional[Exception] = None
    for label, opts in strategies:
        info, last_error = _attempt(opts)
        if info is not None:
            break
        on_progress(0.0, f"Blocked ({label}) — trying another route…")

    # Still blocked: rotate the VPN exit IP and run the whole thing again.
    if info is None and vpn.rotate(reason=str(last_error)[:180]):
        on_progress(0.0, "Switched VPN server, retrying…")
        for _label, opts in strategies:
            info, last_error = _attempt(opts)
            if info is not None:
                break

    if info is None:
        raise RuntimeError(
            f"Download failed: {last_error}. Try enabling VPN rotation in Settings, "
            f"refreshing the YouTube cookies, or running 'pip install -U yt-dlp'.")

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
