"""Generate a burned-in animated caption track (ASS) for a clip.

Classic short-form style: a few uppercase words at a time, highlighted
word-by-word in sync with speech using ASS karaoke (\\k) tags.
"""

from __future__ import annotations

from pathlib import Path

from .transcribe import Word

# ASS colours are &HAABBGGRR.
BASE_COLOR = "&H00FFFFFF"      # white (not-yet-spoken words)
HIGHLIGHT_COLOR = "&H0000E5FF"  # amber (spoken word), BGR of #FFE500
OUTLINE_COLOR = "&H00000000"    # black

WORDS_PER_LINE = 4


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _clean(word: str) -> str:
    return word.strip().replace("{", "").replace("}", "").replace("\n", " ").upper()


def build_ass(words: list[Word], clip_start: float, out_path: Path,
              play_w: int = 1080, play_h: int = 1920) -> Path | None:
    words = [w for w in words if _clean(w.text)]
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
Style: Pop,DejaVu Sans,74,{HIGHLIGHT_COLOR},{BASE_COLOR},{OUTLINE_COLOR},&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,90,90,420,1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    for i in range(0, len(words), WORDS_PER_LINE):
        group = words[i:i + WORDS_PER_LINE]
        g_start = group[0].start - clip_start
        g_end = group[-1].end - clip_start
        if g_end <= 0:
            continue
        g_start = max(0.0, g_start)

        # Karaoke: each word's \k duration tiles to the next word's start so the
        # amber highlight tracks the spoken word.
        parts = []
        for j, w in enumerate(group):
            nxt = group[j + 1].start if j + 1 < len(group) else w.end
            dur_cs = max(1, int(round((nxt - w.start) * 100)))
            parts.append(f"{{\\kf{dur_cs}}}{_clean(w.text)} ")
        text = "".join(parts).strip()
        lines.append(
            f"Dialogue: 0,{_fmt_time(g_start)},{_fmt_time(g_end)},Pop,,0,0,0,,{text}"
        )

    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path
