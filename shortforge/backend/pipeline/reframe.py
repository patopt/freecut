"""Compute the 9:16 crop window for a clip, optionally centred on a face.

This is a pragmatic, per-clip static reframe (not full active-speaker tracking):
we sample a few frames from the clip, find the dominant face, and centre the
vertical crop on its median x position. Falls back to a centre crop.
"""

from __future__ import annotations

import subprocess

TARGET_W = 1080
TARGET_H = 1920
TARGET_RATIO = 9 / 16


def probe_dimensions(path: str) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", path,
        ],
        capture_output=True, text=True, check=True,
    )
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


def _even(n: int) -> int:
    return n - (n % 2)


def compute_crop(src_w: int, src_h: int, center_x_frac: float) -> dict:
    """Return an ffmpeg crop spec {w,h,x,y} for a 9:16 window over the source."""
    src_ratio = src_w / src_h
    if src_ratio > TARGET_RATIO:
        # Source is wider than 9:16 → crop the sides (this is the common case).
        cw = _even(int(round(src_h * TARGET_RATIO)))
        ch = _even(src_h)
        cx = int(round(center_x_frac * src_w - cw / 2))
        cx = max(0, min(cx, src_w - cw))
        cy = 0
    else:
        # Source is taller/narrower → crop top & bottom, keep full width.
        cw = _even(src_w)
        ch = _even(int(round(src_w / TARGET_RATIO)))
        cx = 0
        cy = max(0, (src_h - ch) // 2)
    return {"w": cw, "h": ch, "x": cx, "y": cy}


def detect_face_center(path: str, start: float, end: float, samples: int = 8) -> float:
    """Return the median face-centre x as a fraction 0..1, or 0.5 if none found."""
    try:
        import cv2  # type: ignore
    except Exception:
        return 0.5

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return 0.5

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0.5

    centers: list[float] = []
    duration = max(0.1, end - start)
    try:
        for i in range(samples):
            t = start + duration * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                             minSize=(int(w * 0.05), int(h * 0.05)))
            if len(faces):
                # Largest face in this frame.
                fx, fy, fw, fh = max(faces, key=lambda r: r[2] * r[3])
                centers.append((fx + fw / 2) / w)
    finally:
        cap.release()

    if not centers:
        return 0.5
    centers.sort()
    return centers[len(centers) // 2]
