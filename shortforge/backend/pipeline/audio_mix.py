"""Mix a background-music track under a video's existing audio.

The music is looped to cover the whole clip, lowered to a background level, and
(by default) ducked under the foreground voice so narration stays clear.
Video is stream-copied, so this is a fast, near-lossless second pass.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def add_background_music(
    video_path: str, music_path: str, out_path: Path, gain: float = 0.18, duck: bool = True,
) -> Path:
    gain = max(0.0, min(1.0, gain))
    # Normalise both sources to a common rate/layout so sidechaincompress/amix
    # never fail on a mono/stereo or sample-rate mismatch.
    afmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    if duck:
        # Sidechain-duck the (lowered) music using the original audio as the key,
        # then mix the ducked music back with the original voice.
        fc = (
            f"[1:a]{afmt},volume={gain}[bg];"
            f"[0:a]{afmt},asplit=2[voice][key];"
            f"[bg][key]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=250[bgd];"
            f"[voice][bgd]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        fc = (
            f"[1:a]{afmt},volume={gain}[bg];"
            f"[0:a]{afmt}[voice];"
            f"[voice][bg]amix=inputs=2:duration=first:normalize=0[a]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg music mix failed: {proc.stderr[-800:]}")
    return out_path


def apply_music_in_place(video_path: str, music_path: str, gain: float = 0.18) -> None:
    """Mix music into `video_path`, replacing it (via a temp file)."""
    src = Path(video_path)
    tmp = src.with_name(src.stem + ".music.mp4")
    add_background_music(video_path, music_path, tmp, gain=gain)
    shutil.move(str(tmp), str(src))
