"""Pick the best moments from a transcript using the Gemini API.

Returns a list of highlight dicts: {start, end, title, reason, score}.
Falls back to an even-spacing heuristic when no key is configured or the API
call fails, so the pipeline always produces something.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from .transcribe import Transcript

PROMPT = """You are a viral short-form video editor (like Opus Clip).
Below is a timestamped transcript of a long video. Identify the {count} BEST
self-contained moments to turn into vertical short clips (YouTube Shorts /
TikTok / Reels).

Rules:
- Each clip must be between {min_len} and {max_len} seconds long.
- Prefer moments with a strong hook, a complete thought, emotion, a surprising
  claim, a punchline, or actionable advice. Avoid rambling or mid-sentence cuts.
- Clips must NOT overlap. Spread them across the whole video.
- `start`/`end` are in SECONDS from the beginning of the video.
- `title` is a punchy, curiosity-driven caption (max 60 chars), no hashtags.
- `reason` is one short sentence on why it will perform well.
- `score` is 0-100, your confidence it will go viral.
- Also rate 0-100 the four signals behind that score:
  `hook` (how strongly the first 3 seconds grab attention),
  `flow` (emotional arc / pacing), `value` (payoff for the viewer),
  `trend` (alignment with what performs on short-form now).
- `hook_text` is a punchy 3-6 word on-screen hook for the first seconds.

Return ONLY a JSON array, no prose, shaped like:
[{{"start": 12.4, "end": 48.9, "title": "...", "reason": "...", "score": 87,
   "hook": 90, "flow": 80, "value": 85, "trend": 75, "hook_text": "..."}}]

TRANSCRIPT:
{transcript}
"""


def _extract_json_array(text: str) -> list:
    text = text.strip()
    # Strip code fences if the model wrapped the JSON.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # Some models wrap in {"highlights": [...]}.
            for v in data.values():
                if isinstance(v, list):
                    return v
            return []
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
    return []


def _sanitize(raw: list, duration: float, min_len: float, max_len: float, count: int) -> list[dict]:
    out: list[dict] = []
    for item in raw:
        try:
            start = max(0.0, float(item["start"]))
            end = min(duration, float(item["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < min_len:
            end = min(duration, start + min_len)
        if end - start > max_len:
            end = start + max_len
        if end <= start:
            continue
        def _sub(key: str) -> float:
            try:
                return max(0.0, min(100.0, float(item.get(key, 0) or 0)))
            except (TypeError, ValueError):
                return 0.0

        out.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "title": str(item.get("title", "")).strip()[:80] or "Highlight",
            "reason": str(item.get("reason", "")).strip()[:200],
            "score": float(item.get("score", 50) or 50),
            # Virality breakdown (hook / emotional flow / value / trend).
            "hook": _sub("hook"),
            "flow": _sub("flow"),
            "value": _sub("value"),
            "trend": _sub("trend"),
            "hook_text": str(item.get("hook_text", "")).strip()[:60],
        })
    # Drop overlaps, keep highest score first.
    out.sort(key=lambda h: h["score"], reverse=True)
    chosen: list[dict] = []
    for h in out:
        if all(h["end"] <= c["start"] or h["start"] >= c["end"] for c in chosen):
            chosen.append(h)
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda h: h["start"])
    return chosen


def _heuristic(duration: float, min_len: float, max_len: float, count: int) -> list[dict]:
    if duration <= 0:
        return []
    clip = min(max_len, max(min_len, 40.0))
    n = max(1, min(count, int(duration // clip)))
    step = duration / n
    out = []
    for i in range(n):
        start = i * step + max(0, (step - clip) / 2)
        out.append({
            "start": round(start, 2),
            "end": round(min(duration, start + clip), 2),
            "title": f"Highlight {i + 1}",
            "reason": "Auto-selected (no LLM key configured).",
            "score": 50.0,
        })
    return out


def find_highlights(
    transcript: Transcript,
    *,
    api_key: str,
    model: str,
    count: int,
    min_len: float = 60.0,
    max_len: float = 90.0,
) -> tuple[list[dict], Optional[str]]:
    """Return (highlights, note). `note` describes any fallback that happened."""
    duration = transcript.duration
    if not api_key:
        return _heuristic(duration, min_len, max_len, count), "No Gemini key — used even-spacing fallback."

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = PROMPT.format(
            count=count, min_len=int(min_len), max_len=int(max_len),
            transcript=transcript.plain_text_with_timestamps()[:120_000],
        )
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.5,
            ),
        )
        raw = _extract_json_array(resp.text or "")
        highlights = _sanitize(raw, duration, min_len, max_len, count)
        if highlights:
            return highlights, None
        return _heuristic(duration, min_len, max_len, count), "Gemini returned no usable clips — used fallback."
    except Exception as exc:  # noqa: BLE001 — surface any SDK/network error as a note
        return _heuristic(duration, min_len, max_len, count), f"Gemini call failed ({exc}) — used fallback."
