"""Transcribe the source with faster-whisper, keeping word-level timestamps."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable

ProgressCb = Callable[[float, str], None]

# Whisper is the memory hog (~700 MB per int8 instance) and a model object is
# NOT safe to share across threads, so each worker keeps its own and a
# semaphore caps how many transcriptions decode at the same time.
_local = threading.local()


def _total_ram_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except Exception:  # noqa: BLE001
        pass
    return 4.0


def _default_slots() -> int:
    """One transcription per ~3 GB of RAM.

    A long video decoded by two Whisper instances at once is exactly what got
    the process OOM-killed on a 4 GB box, so small machines get a single slot.
    """
    return 1 if _total_ram_gb() < 6 else 2


_slots = threading.Semaphore(
    max(1, min(4, int(os.environ.get("TRANSCRIBE_SLOTS", _default_slots())))))


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str
    duration: float
    segments: list[Segment]

    def plain_text_with_timestamps(self) -> str:
        """Compact transcript for the LLM: '[mm:ss] text' per segment."""
        lines = []
        for s in self.segments:
            m, sec = divmod(int(s.start), 60)
            lines.append(f"[{m:02d}:{sec:02d}] {s.text.strip()}")
        return "\n".join(lines)

    def words_between(self, start: float, end: float) -> list[Word]:
        out = []
        for seg in self.segments:
            for w in seg.words:
                if w.end > start and w.start < end:
                    out.append(w)
        return out


def _get_model(size: str):
    from faster_whisper import WhisperModel

    cache = getattr(_local, "models", None)
    if cache is None:
        cache = _local.models = {}
    _model_cache = cache
    if size not in _model_cache:
        # int8 keeps it usable on a CPU-only VPS; auto-detects CUDA if present.
        try:
            _model_cache[size] = WhisperModel(size, device="auto", compute_type="int8")
        except Exception:
            _model_cache[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _model_cache[size]


def transcribe(path: str, model_size: str, duration: float, on_progress: ProgressCb) -> Transcript:
    with _slots:
        return _transcribe_locked(path, model_size, duration, on_progress)


def _transcribe_locked(path: str, model_size: str, duration: float,
                       on_progress: ProgressCb) -> Transcript:
    model = _get_model(model_size)
    seg_iter, info = model.transcribe(
        path, word_timestamps=True, vad_filter=True, beam_size=1,
    )
    total = duration or info.duration or 0
    segments: list[Segment] = []
    # Long sources are the ones that push a small VPS into swap, so drop the
    # model afterwards instead of keeping it resident per worker thread.
    release_after = total > 45 * 60
    for seg in seg_iter:
        words = [Word(w.start, w.end, w.word) for w in (seg.words or [])]
        segments.append(Segment(seg.start, seg.end, seg.text, words))
        if total:
            on_progress(min(seg.end / total, 1.0), f"Transcribing {int(min(seg.end/total,1.0)*100)}%")
    if release_after:
        release_model(model_size)
    return Transcript(language=info.language, duration=total, segments=segments)


def release_model(size: str) -> None:
    """Free this thread's Whisper model and return the memory to the OS."""
    import gc

    cache = getattr(_local, "models", None)
    if cache and size in cache:
        del cache[size]
    gc.collect()
