"""Text-to-speech dubbing track, time-aligned to the original segments.

Primary engine: **Kokoro-82M** via onnxruntime — realistic, Apache-2.0, runs
fully offline on CPU (no network, which is why it replaces edge-tts as default).
edge-tts is kept as a fallback for languages Kokoro doesn't cover.

Each translated segment is synthesised, placed at its original start time, and
sped up (atempo, capped 2x) to fit its slot so the dub stays in sync with the
picture to within ~1 second.
"""

from __future__ import annotations

import asyncio
import subprocess
import urllib.request
import wave
from pathlib import Path
from typing import Callable

from .. import config, db
from .transcribe import Segment

# --- Kokoro config ----------------------------------------------------------
# lang -> (voice, kokoro language code). Kokoro v1.0 covers these; anything
# else falls back to edge-tts.
KOKORO_VOICES = {
    "fr": ("ff_siwis", "fr-fr"),
    "en": ("af_heart", "en-us"),
    "es": ("ef_dora", "es"),
    "it": ("if_sara", "it"),
    "pt": ("pf_dora", "pt-br"),
    "hi": ("hf_alpha", "hi"),
    "ja": ("jf_alpha", "ja"),
    "zh": ("zf_xiaobei", "zh"),
}
KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# edge-tts fallback voices (languages Kokoro doesn't cover).
EDGE_VOICES = {
    "de": "de-DE-KatjaNeural", "ko": "ko-KR-SunHiNeural", "ar": "ar-EG-SalmaNeural",
    "ru": "ru-RU-SvetlanaNeural", "nl": "nl-NL-ColetteNeural", "pl": "pl-PL-ZofiaNeural",
    "tr": "tr-TR-EmelNeural", "fr": "fr-FR-DeniseNeural", "en": "en-US-AriaNeural",
    "es": "es-ES-ElviraNeural",
}

Log = Callable[[str], None]
_kokoro = None


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


# --- Kokoro engine ----------------------------------------------------------

def _ensure_kokoro_model(log: Log) -> tuple[Path, Path]:
    models = config.DATA_DIR / "models"
    models.mkdir(parents=True, exist_ok=True)
    onnx = models / "kokoro-v1.0.onnx"
    voices = models / "voices-v1.0.bin"
    for path, url in ((onnx, KOKORO_MODEL_URL), (voices, KOKORO_VOICES_URL)):
        if not path.exists() or path.stat().st_size == 0:
            log(f"Downloading Kokoro model ({path.name}, one-time)…")
            urllib.request.urlretrieve(url, path)
    return onnx, voices


def _get_kokoro(log: Log):
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        onnx, voices = _ensure_kokoro_model(log)
        _kokoro = Kokoro(str(onnx), str(voices))
    return _kokoro


def _kokoro_synth(text: str, lang: str, out_path: Path, log: Log) -> bool:
    voice, klang = KOKORO_VOICES[lang]
    kokoro = _get_kokoro(log)
    samples, sr = kokoro.create(text, voice=voice, speed=1.0, lang=klang)
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return out_path.exists() and out_path.stat().st_size > 0


# --- edge-tts fallback engine ----------------------------------------------

async def _edge_save(text: str, voice: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice).save(str(path))


def _edge_synth(text: str, lang: str, out_path: Path) -> bool:
    voice = EDGE_VOICES.get(lang, EDGE_VOICES["en"])
    asyncio.run(_edge_save(text, voice, out_path))
    return out_path.exists() and out_path.stat().st_size > 0


# --- dispatch + timed assembly ---------------------------------------------

def _synth_segment(text: str, lang: str, out_path: Path, log: Log) -> bool:
    """Synthesise one segment with the configured engine (Kokoro default)."""
    engine = (db.effective("tts_engine") or "kokoro").lower()
    if engine == "kokoro" and lang in KOKORO_VOICES:
        try:
            return _kokoro_synth(text, lang, out_path.with_suffix(".wav"), log)
        except Exception as exc:  # noqa: BLE001 — fall back to edge on any Kokoro error
            log(f"Kokoro failed ({exc}); trying edge-tts")
    return _edge_synth(text, lang, out_path.with_suffix(".mp3"))


def build_dub_track(
    segments: list[Segment],
    translations: list[str],
    lang: str,
    video_duration: float,
    workdir: Path,
    out_path: Path,
    log: Log = lambda _m: None,
) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)

    clips: list[tuple[Path, float, float]] = []  # (audio, start, fit_tempo)
    for i, seg in enumerate(segments):
        text = (translations[i] if i < len(translations) else "").strip()
        if not text:
            continue
        base = workdir / f"seg_{i:03d}"
        try:
            ok = _synth_segment(text, lang, base, log)
        except Exception as exc:  # noqa: BLE001
            log(f"Segment {i} TTS error: {exc}")
            ok = False
        if not ok:
            continue
        audio = base.with_suffix(".wav")
        if not audio.exists():
            audio = base.with_suffix(".mp3")
        if not audio.exists():
            continue
        dur = _probe_duration(audio)
        next_start = segments[i + 1].start if i + 1 < len(segments) else video_duration
        slot = max(0.5, next_start - seg.start)
        tempo = min(2.0, dur / slot) if dur > slot else 1.0
        clips.append((audio, seg.start, tempo))

    if not clips:
        raise RuntimeError("TTS produced no audio segments")

    # One ffmpeg call: fit + delay each clip, mix onto a common timeline.
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for idx, (audio, start, tempo) in enumerate(clips):
        inputs += ["-i", str(audio)]
        delay_ms = int(start * 1000)
        filters.append(
            f"[{idx}:a]aresample=48000,atempo={tempo:.4f},adelay={delay_ms}:all=1[a{idx}]"
        )
        labels.append(f"[a{idx}]")
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
