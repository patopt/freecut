"""Render a single vertical short with ffmpeg: cut → 9:16 crop → burn captions."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .reframe import target_size


def _escape_sub_path(path: Path) -> str:
    # Escaping rules for the ffmpeg `subtitles` filter argument.
    s = str(path)
    s = s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return s


def render_short(
    source_path: str,
    start: float,
    end: float,
    crop: dict,
    ass_path: Path | None,
    out_path: Path,
    thumb_path: Path,
    aspect: str = "9:16",
) -> dict:
    duration = max(0.1, end - start)
    tw, th = target_size(aspect)
    vf = f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']},scale={tw}:{th}"
    if ass_path is not None:
        vf += f",subtitles={_escape_sub_path(ass_path)}"

    cmd = [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", source_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {proc.stderr[-800:]}")

    # Thumbnail from ~1s into the clip.
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "1", "-i", str(out_path),
            "-frames:v", "1", "-vf", "scale=360:-1", str(thumb_path),
        ],
        capture_output=True, text=True,
    )

    return {"path": str(out_path), "thumb": str(thumb_path), "width": tw, "height": th}
