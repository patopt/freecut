"""Mix a background-music track under a video's existing audio.

The music (any format ffmpeg reads: mp3/m4a/wav/…) is looped to cover the clip,
lowered to a background level, and — when the video already has audio — ducked
under the foreground voice. Robust fallbacks keep it working across ffmpeg
builds and on videos that have no audio track at all.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _has_audio(path: str) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _run(cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0, proc.stderr[-800:]


def add_background_music(
    video_path: str, music_path: str, out_path: Path, gain: float = 0.18, duck: bool = True,
) -> Path:
    gain = max(0.0, min(1.0, gain))
    afmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    common_tail = ["-map", "0:v", "-map", "[a]", "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                   "-shortest", str(out_path)]
    base = ["ffmpeg", "-y", "-nostats", "-loglevel", "error",
            "-i", video_path, "-stream_loop", "-1", "-i", music_path]

    attempts: list[str] = []
    if _has_audio(video_path):
        if duck:
            attempts.append(
                f"[1:a]{afmt},volume={gain}[bg];[0:a]{afmt},asplit=2[voice][key];"
                f"[bg][key]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=250[bgd];"
                f"[voice][bgd]amix=inputs=2:duration=first:normalize=0[a]"
            )
        attempts.append(  # simple mix (fallback / duck=False)
            f"[1:a]{afmt},volume={gain}[bg];[0:a]{afmt}[voice];"
            f"[voice][bg]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        # No voice track — music becomes the sole audio, trimmed to the video.
        attempts.append(f"[1:a]{afmt},volume={min(1.0, gain * 3)}[a]")

    last_err = ""
    for fc in attempts:
        ok, err = _run([*base, "-filter_complex", fc, *common_tail])
        if ok:
            return out_path
        last_err = err
    raise RuntimeError(f"ffmpeg music mix failed: {last_err}")


def apply_music_in_place(video_path: str, music_path: str, gain: float = 0.18) -> None:
    """Mix music into `video_path`, replacing it (via a temp file)."""
    src = Path(video_path)
    tmp = src.with_name(src.stem + ".music.mp4")
    add_background_music(video_path, music_path, tmp, gain=gain)
    shutil.move(str(tmp), str(src))
