"""Text-to-speech dubbing track, time-aligned to the original segments.

Uses edge-tts (free, multilingual, no API key). Each translated segment is
synthesised, then placed at its original start time; if the synthesised speech
is longer than the gap to the next segment it is sped up (atempo, capped at 2x)
so it fits — keeping the dub in sync with the picture to within ~1 second.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from .transcribe import Segment

VOICES = {
    "en": "en-US-AriaNeural", "fr": "fr-FR-DeniseNeural", "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural", "pt": "pt-BR-FranciscaNeural", "it": "it-IT-ElsaNeural",
    "ja": "ja-JP-NanamiNeural", "ko": "ko-KR-SunHiNeural", "zh": "zh-CN-XiaoxiaoNeural",
    "ar": "ar-EG-SalmaNeural", "ru": "ru-RU-SvetlanaNeural", "hi": "hi-IN-SwaraNeural",
    "nl": "nl-NL-ColetteNeural", "pl": "pl-PL-ZofiaNeural", "tr": "tr-TR-EmelNeural",
}


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


async def _synth_one(text: str, voice: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice).save(str(path))


def build_dub_track(
    segments: list[Segment],
    translations: list[str],
    lang: str,
    video_duration: float,
    workdir: Path,
    out_path: Path,
) -> Path:
    voice = VOICES.get(lang, VOICES["en"])
    workdir.mkdir(parents=True, exist_ok=True)

    clips: list[tuple[Path, float, float]] = []  # (mp3, start, fit_tempo)
    for i, seg in enumerate(segments):
        text = (translations[i] if i < len(translations) else "").strip()
        if not text:
            continue
        mp3 = workdir / f"seg_{i:03d}.mp3"
        try:
            asyncio.run(_synth_one(text, voice, mp3))
        except Exception:
            continue
        if not mp3.exists() or mp3.stat().st_size == 0:
            continue
        dur = _probe_duration(mp3)
        next_start = segments[i + 1].start if i + 1 < len(segments) else video_duration
        slot = max(0.5, next_start - seg.start)
        tempo = 1.0
        if dur > slot:
            tempo = min(2.0, dur / slot)
        clips.append((mp3, seg.start, tempo))

    if not clips:
        raise RuntimeError("TTS produced no audio segments")

    # One ffmpeg call: delay + fit each clip, mix them onto a common timeline.
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for idx, (mp3, start, tempo) in enumerate(clips):
        inputs += ["-i", str(mp3)]
        delay_ms = int(start * 1000)
        label = f"a{idx}"
        filters.append(
            f"[{idx}:a]aresample=48000,atempo={tempo:.4f},"
            f"adelay={delay_ms}:all=1[{label}]"
        )
        labels.append(f"[{label}]")
    mix = "".join(labels) + f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0[mix]"
    filter_complex = ";".join(filters + [mix])

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[mix]",
        "-t", f"{max(video_duration, 0.5):.3f}",
        "-c:a", "aac", "-b:a", "160k",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg TTS mix failed: {proc.stderr[-800:]}")
    return out_path
