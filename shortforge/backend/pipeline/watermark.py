"""Optional text watermark burned into every rendered video.

Enabled from Settings. The text is drawn by ffmpeg's `drawtext` filter, so it
is part of the pixels — it survives re-uploads and re-encodes, which is the
point of using it to mark ownership.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import db

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left",
             "bottom-center", "top-center", "center")


def font_path() -> Optional[str]:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _escape(text: str) -> str:
    """Escape for a drawtext value inside an ffmpeg filter argument.

    Order matters: backslashes first, or the escapes added below get escaped
    a second time. Percent is special to drawtext's own expansion.
    """
    out = text.replace("\\", "\\\\")
    for ch in (":", "'", "%", ",", "[", "]", ";"):
        out = out.replace(ch, "\\" + ch)
    return out


def _xy(position: str, margin: int) -> str:
    return {
        "bottom-right": f"x=w-tw-{margin}:y=h-th-{margin}",
        "bottom-left": f"x={margin}:y=h-th-{margin}",
        "top-right": f"x=w-tw-{margin}:y={margin}",
        "top-left": f"x={margin}:y={margin}",
        "bottom-center": f"x=(w-tw)/2:y=h-th-{margin}",
        "top-center": f"x=(w-tw)/2:y={margin}",
        "center": "x=(w-tw)/2:y=(h-th)/2",
    }.get(position, f"x=w-tw-{margin}:y=h-th-{margin}")


def settings() -> dict:
    """Current watermark configuration, normalised."""
    position = db.get_setting("watermark_position") or "bottom-right"
    try:
        opacity = float(db.get_setting("watermark_opacity") or 0.35)
    except (TypeError, ValueError):
        opacity = 0.35
    try:
        size = float(db.get_setting("watermark_size") or 3.0)
    except (TypeError, ValueError):
        size = 3.0
    return {
        "enabled": (db.get_setting("watermark_enabled") or "0") == "1",
        "text": (db.get_setting("watermark_text") or "").strip(),
        "position": position if position in POSITIONS else "bottom-right",
        "opacity": max(0.05, min(1.0, opacity)),
        # Percent of the video height, so the mark looks the same at any size.
        "size": max(1.0, min(12.0, size)),
    }


def build_filter(height: int) -> Optional[str]:
    """A drawtext filter fragment, or None when the watermark is off.

    `height` drives the font size so a 1920-tall clip and a 1080-tall one get a
    proportionally identical mark.
    """
    cfg = settings()
    if not cfg["enabled"] or not cfg["text"]:
        return None
    font = font_path()
    if not font:
        return None  # no usable font: silently skip rather than fail the render

    fontsize = max(12, int(height * cfg["size"] / 100.0))
    parts = [
        f"fontfile={_escape(font)}",
        f"text={_escape(cfg['text'])}",
        f"fontsize={fontsize}",
        f"fontcolor=white@{cfg['opacity']:.2f}",
        # A soft shadow keeps it legible over both bright and dark footage
        # without the heavy box that would spoil the frame.
        f"shadowcolor=black@{min(1.0, cfg['opacity'] + 0.25):.2f}",
        "shadowx=2", "shadowy=2",
        _xy(cfg["position"], max(8, int(height * 0.025))),
    ]
    return "drawtext=" + ":".join(parts)


def append_to(vf: str, height: int) -> str:
    """Append the watermark to an existing -vf chain (last, so it sits on top)."""
    fragment = build_filter(height)
    if not fragment:
        return vf
    return f"{vf},{fragment}" if vf else fragment
