"""Burned-in animated captions, with selectable viral-style presets.

Styles are built from word-level timings, so every preset stays in sync with
speech. Presets follow what performs on short-form in 2026: karaoke highlight,
Hormozi-style big bold text, word-by-word pop, and clean minimal subtitles.
"""

from __future__ import annotations

from pathlib import Path

from .transcribe import Word

# ASS colours are &HAABBGGRR (alpha, blue, green, red).
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"
AMBER = "&H0000E5FF"    # #FFE500
GREEN = "&H0000FF66"    # #66FF00
CYAN = "&H00FFFF00"     # #00FFFF
SHADOW = "&H64000000"

# name -> settings. `mode` drives how lines are built:
#   karaoke  = all words shown, active word highlighted (\kf)
#   pop      = only the current word(s) on screen, punchy
#   plain    = static phrase, no per-word highlight
PRESETS: dict[str, dict] = {
    "karaoke": {
        "label": "Karaoke (highlight follows speech)",
        "mode": "karaoke", "font": "DejaVu Sans", "size": 74, "bold": -1,
        "primary": AMBER, "secondary": WHITE, "outline_col": BLACK,
        "outline": 5, "shadow": 2, "margin_v": 420, "words": 4, "uppercase": True,
    },
    "hormozi": {
        "label": "Hormozi (big bold, yellow keyword)",
        "mode": "karaoke", "font": "DejaVu Sans", "size": 96, "bold": -1,
        "primary": AMBER, "secondary": WHITE, "outline_col": BLACK,
        "outline": 8, "shadow": 3, "margin_v": 520, "words": 3, "uppercase": True,
    },
    "pop": {
        "label": "Pop (one word at a time)",
        "mode": "pop", "font": "DejaVu Sans", "size": 104, "bold": -1,
        "primary": WHITE, "secondary": WHITE, "outline_col": BLACK,
        "outline": 8, "shadow": 3, "margin_v": 500, "words": 1, "uppercase": True,
    },
    "neon": {
        "label": "Neon (green karaoke)",
        "mode": "karaoke", "font": "DejaVu Sans", "size": 78, "bold": -1,
        "primary": GREEN, "secondary": WHITE, "outline_col": BLACK,
        "outline": 6, "shadow": 2, "margin_v": 430, "words": 4, "uppercase": True,
    },
    "cyan": {
        "label": "Cyan karaoke",
        "mode": "karaoke", "font": "DejaVu Sans", "size": 78, "bold": -1,
        "primary": CYAN, "secondary": WHITE, "outline_col": BLACK,
        "outline": 6, "shadow": 2, "margin_v": 430, "words": 4, "uppercase": True,
    },
    "minimal": {
        "label": "Minimal (clean subtitles)",
        "mode": "plain", "font": "DejaVu Sans", "size": 58, "bold": 0,
        "primary": WHITE, "secondary": WHITE, "outline_col": BLACK,
        "outline": 3, "shadow": 1, "margin_v": 300, "words": 7, "uppercase": False,
    },
}
DEFAULT_PRESET = "karaoke"


def list_presets() -> dict[str, str]:
    return {k: v["label"] for k, v in PRESETS.items()}


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _clean(word: str, upper: bool) -> str:
    w = word.strip().replace("{", "").replace("}", "").replace("\n", " ")
    return w.upper() if upper else w


def build_ass(words: list[Word], clip_start: float, out_path: Path,
              play_w: int = 1080, play_h: int = 1920,
              preset: str = DEFAULT_PRESET) -> Path | None:
    cfg = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    upper = cfg["uppercase"]
    words = [w for w in words if _clean(w.text, upper)]
    if not words:
        return None

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cfg['font']},{cfg['size']},{cfg['primary']},{cfg['secondary']},{cfg['outline_col']},{SHADOW},{cfg['bold']},0,0,0,100,100,0,0,1,{cfg['outline']},{cfg['shadow']},2,90,90,{cfg['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    per_line = max(1, int(cfg["words"]))
    mode = cfg["mode"]

    if mode == "pop":
        # One word (or few) at a time, with a quick scale-in for punch.
        for i in range(0, len(words), per_line):
            group = words[i:i + per_line]
            start = max(0.0, group[0].start - clip_start)
            end = max(start + 0.05, group[-1].end - clip_start)
            text = " ".join(_clean(w.text, upper) for w in group)
            effect = "{\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}"
            lines.append(
                f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Cap,,0,0,0,,{effect}{text}")
    else:
        for i in range(0, len(words), per_line):
            group = words[i:i + per_line]
            g_start = max(0.0, group[0].start - clip_start)
            g_end = group[-1].end - clip_start
            if g_end <= 0:
                continue
            if mode == "karaoke":
                parts = []
                for j, w in enumerate(group):
                    nxt = group[j + 1].start if j + 1 < len(group) else w.end
                    dur_cs = max(1, int(round((nxt - w.start) * 100)))
                    parts.append(f"{{\\kf{dur_cs}}}{_clean(w.text, upper)} ")
                text = "".join(parts).strip()
            else:  # plain
                text = " ".join(_clean(w.text, upper) for w in group)
            lines.append(
                f"Dialogue: 0,{_fmt_time(g_start)},{_fmt_time(g_end)},Cap,,0,0,0,,{text}")

    if not lines:
        return None
    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def build_ass_from_segments(segments, translations: list[str], clip_start: float,
                            out_path: Path, play_w: int = 1080, play_h: int = 1920,
                            preset: str = DEFAULT_PRESET) -> Path | None:
    """Captions for dubbed video: distribute each translated line over its
    segment's time span (we have no word timings for translated text)."""
    cfg = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    upper = cfg["uppercase"]
    pseudo: list[Word] = []
    for i, seg in enumerate(segments):
        text = (translations[i] if i < len(translations) else "").strip()
        if not text:
            continue
        toks = [t for t in text.split() if t]
        if not toks:
            continue
        span = max(0.2, seg.end - seg.start)
        step = span / len(toks)
        for k, tok in enumerate(toks):
            ws = seg.start + k * step
            pseudo.append(Word(ws, ws + step, tok))
    if not pseudo:
        return None
    _ = upper  # handled inside build_ass via the preset
    return build_ass(pseudo, clip_start, out_path, play_w, play_h, preset)
