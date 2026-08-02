"""Translate transcript segments with Gemini, preserving 1:1 segment alignment.

Keeping the same number of segments (in the same order) is what lets the dubbed
audio stay time-synced with the original: segment i keeps original start time
`start_i`, only its text changes language.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from . import gemini_errors
from .transcribe import Segment

LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "de": "German",
    "pt": "Portuguese", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ar": "Arabic", "ru": "Russian", "hi": "Hindi",
    "nl": "Dutch", "pl": "Polish", "tr": "Turkish",
}

PROMPT = """Translate each numbered line of this video narration into {lang}.
This is a scene-by-scene movie description voiceover.

Rules:
- Return EXACTLY {n} translations, one per input line, in the same order.
- Keep each translation roughly the same spoken length as the original so the
  dub stays in sync with the picture.
- Natural, fluent {lang}. Translate meaning, not word-for-word.
- Return ONLY a JSON array of {n} strings, nothing else.

LINES:
{lines}
"""


def _parse_array(text: str, n: int) -> Optional[list[str]]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, flags=re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        return None
    out = [str(x) for x in data]
    if len(out) == n:
        return out
    # Length mismatch: pad/truncate so alignment is preserved rather than failing.
    if len(out) > n:
        return out[:n]
    return out + [""] * (n - len(out))


def translate_texts(
    texts: list[str], target_lang: str, *, api_key: str, model: str,
) -> list[str]:
    """Translate arbitrary texts (e.g. title, description). Best-effort."""
    non_empty = [t for t in texts if t.strip()]
    if not api_key or not non_empty:
        return texts
    lang_name = LANGUAGE_NAMES.get(target_lang, target_lang)
    lines = "\n".join(f"{i + 1}. {t.strip() or '(empty)'}" for i, t in enumerate(texts))
    prompt = (
        f"Translate each numbered item into natural {lang_name}. Keep the same "
        f"number of items ({len(texts)}) in order. Return ONLY a JSON array of "
        f"{len(texts)} strings.\n\nITEMS:\n{lines}"
    )
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model, contents=prompt[:60_000],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.3),
        )
        parsed = _parse_array(resp.text or "", len(texts))
        if parsed is None:
            return texts
        return [p if p.strip() else texts[i] for i, p in enumerate(parsed)]
    except Exception:  # noqa: BLE001
        return texts


def translate_segments(
    segments: list[Segment], target_lang: str, *, api_key: str, model: str,
) -> tuple[list[str], Optional[str]]:
    """Return (translations aligned to segments, note)."""
    texts = [s.text.strip() for s in segments]
    if not api_key:
        return texts, "No Gemini key — kept original language (no translation)."

    lang_name = LANGUAGE_NAMES.get(target_lang, target_lang)
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model,
            contents=PROMPT.format(lang=lang_name, n=len(texts), lines=lines[:120_000]),
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.3,
            ),
        )
        parsed = _parse_array(resp.text or "", len(texts))
        if parsed is None:
            return texts, "Translation parse failed — kept original text."
        # Fall back to original where a translation came back empty.
        merged = [p if p.strip() else texts[i] for i, p in enumerate(parsed)]
        return merged, None
    except Exception as exc:  # noqa: BLE001
        return texts, f"Translation failed: {gemini_errors.explain(exc)}"
