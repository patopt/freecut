"""Dub one channel short into a target language, keeping segment timing.

download original -> transcribe -> translate -> timed TTS track -> mux over
the original video (original audio dropped, translated voiceover in its place).
"""

from __future__ import annotations

import os

import shutil
import subprocess
from pathlib import Path

from .. import config, db
from . import (audio_mix, captions, download, reframe, transcribe, translate,
               tts, watermark)

# Cap per-process threads: several renders run in parallel now, and letting
# each ffmpeg grab every core makes them all slower.
FFMPEG_THREADS = os.environ.get("FFMPEG_THREADS", "2")


def run_dub(dub_id: str) -> None:
    dub = db.get_dub(dub_id)
    if not dub:
        return
    short = db.get_channel_short(dub["short_id"])
    if not short:
        db.update_dub(dub_id, status="error", error="Source short missing")
        return

    lang = dub["lang"]
    work = config.WORK_DIR / f"dub_{dub_id}"
    work.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download original -------------------------------------------------
        db.update_dub(dub_id, status="downloading", stage="Downloading", progress=2)
        db.append_dub_log(dub_id, f"Downloading {short['url']}")
        src = download.download_source(
            short["url"], f"dub_{dub_id}",
            lambda f, m: db.update_dub(dub_id, progress=int(2 + 23 * f), message=m),
        )
        # Capture the original title, description and tags for reuse.
        tags_str = ", ".join(src.get("tags", []))
        db.update_dub(dub_id, title=src.get("title", ""),
                      description=src.get("description", ""), tags=tags_str)
        db.append_dub_log(dub_id, f"Downloaded ({src['duration']:.0f}s), {len(src.get('tags', []))} tags")

        # 2. Transcribe --------------------------------------------------------
        db.update_dub(dub_id, status="transcribing", stage="Transcribing", progress=25)
        whisper_model = db.effective("whisper_model") or "small"
        tr = transcribe.transcribe(
            src["path"], whisper_model, src["duration"],
            lambda f, m: db.update_dub(dub_id, progress=int(25 + 30 * f), message=m),
        )
        if not tr.segments:
            raise RuntimeError("No speech detected in the original short.")
        db.append_dub_log(dub_id, f"Transcribed {len(tr.segments)} segments ({tr.language})")

        # 3. Translate ---------------------------------------------------------
        db.update_dub(dub_id, status="translating", stage="Translating", progress=58)
        api_key = db.effective("gemini_api_key")
        model = db.effective("gemini_model") or "gemini-2.5-pro"
        translations, note = translate.translate_segments(
            tr.segments, lang, api_key=api_key, model=model,
        )
        if note:
            db.append_dub_log(dub_id, note)
        # Also translate the title, description and tags for re-posting.
        orig_tags = src.get("tags", []) or []
        payload = [src.get("title", ""), src.get("description", "")] + orig_tags
        translated_meta = translate.translate_texts(payload, lang, api_key=api_key, model=model)
        tr_title = translated_meta[0] if translated_meta else src.get("title", "")
        tr_desc = translated_meta[1] if len(translated_meta) > 1 else src.get("description", "")
        tr_tags = [t.strip() for t in translated_meta[2:] if t.strip()]
        db.update_dub(dub_id, tr_title=tr_title, tr_description=tr_desc,
                      tags=", ".join(tr_tags) if tr_tags else tags_str)
        if tr_tags:
            db.append_dub_log(dub_id, f"Translated {len(tr_tags)} tags")

        # 4. TTS (timed) -------------------------------------------------------
        db.update_dub(dub_id, status="dubbing", stage="Generating voice", progress=66)
        db.append_dub_log(dub_id, f"Synthesizing {lang} voiceover")
        audio_path = tts.build_dub_track(
            tr.segments, translations, lang, tr.duration or src["duration"],
            work, work / "dub_audio.m4a",
            log=lambda m: db.append_dub_log(dub_id, m),
        )
        voice_dur = tts._probe_duration(audio_path)
        db.append_dub_log(dub_id, f"Voice track: {voice_dur:.1f}s")
        if voice_dur < 0.2:
            raise RuntimeError("Voice track is empty — check the TTS log above.")

        # 5. Mux over the original video --------------------------------------
        db.update_dub(dub_id, status="rendering", stage="Muxing", progress=90)
        out_dir = config.OUTPUT_DIR / "dubs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{dub_id}.mp4"
        thumb_file = out_dir / f"{dub_id}.jpg"
        _mux(src["path"], audio_path, out_file)

        # Captions and watermark share one encode pass — doing them separately
        # would re-encode the whole video twice for no benefit.
        style = dub.get("caption_style") or ""
        ass = None
        if style and style != "none":
            try:
                ass = captions.build_ass_from_segments(
                    tr.segments, translations, 0.0, work / "captions.ass", preset=style)
            except Exception as exc:  # noqa: BLE001
                db.append_dub_log(dub_id, f"Captions skipped: {exc}")
        try:
            burned = _burn_overlays(out_file, ass)
            if burned:
                db.append_dub_log(dub_id, f"Burned in: {', '.join(burned)}")
        except Exception as exc:  # noqa: BLE001
            db.append_dub_log(dub_id, f"Overlay pass skipped: {exc}")

        # Optional background music under the voiceover.
        music_id = dub.get("music_id")
        if music_id:
            track = db.get_music(music_id)
            if track and track.get("path"):
                if not Path(track["path"]).exists():
                    db.append_dub_log(dub_id, "Music file missing on disk — skipped")
                else:
                    try:
                        audio_mix.apply_music_in_place(str(out_file), track["path"], gain=0.16)
                        db.append_dub_log(dub_id, f"Background music mixed in ({track['name']})")
                    except Exception as exc:  # noqa: BLE001
                        db.append_dub_log(dub_id, f"Music mix skipped: {exc}")
            else:
                db.append_dub_log(dub_id, "Selected music not found — skipped")

        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", str(out_file), "-frames:v", "1",
             "-vf", "scale=360:-1", str(thumb_file)],
            capture_output=True, text=True,
        )

        db.update_dub(dub_id, status="done", stage="Done", progress=100,
                      path=str(out_file), thumb=str(thumb_file), message="Dub ready")
        db.append_dub_log(dub_id, "Dub complete.")
        db.log_activity("dub", f"Translated to {lang.upper()}: {tr_title or short.get('title', '')}",
                        f"Voice {voice_dur:.0f}s · {len(tr.segments)} segments",
                        "success", "dub", dub_id)
    except Exception as exc:  # noqa: BLE001
        db.append_dub_log(dub_id, f"ERROR: {exc}")
        db.update_dub(dub_id, status="error", stage="Failed", error=str(exc))
        db.log_activity("dub", f"Translation failed: {short.get('title', '')}",
                        str(exc)[:400], "error", "dub", dub_id)


def _burn_overlays(video_path: Path, ass_path: Path | None) -> list[str]:
    """Burn captions and/or the watermark in one pass, replacing the file.

    Returns the labels of what was applied, empty when there was nothing to do
    (in which case the video is left untouched — no needless re-encode).
    """
    parts: list[str] = []
    applied: list[str] = []
    if ass_path is not None:
        esc = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"subtitles={esc}")
        applied.append("captions")

    _, height = reframe.probe_dimensions(str(video_path))
    mark = watermark.build_filter(height or 1920)
    if mark:
        parts.append(mark)   # after the subtitles, so it stays on top
        applied.append("watermark")

    if not parts:
        return []

    tmp = video_path.with_name(video_path.stem + ".ovl.mp4")
    cmd = [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", ",".join(parts),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", FFMPEG_THREADS, "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError((proc.stderr or "").strip()[-400:])
    shutil.move(str(tmp), str(video_path))
    return applied


def _mux(video_path: str, audio_path: Path, out_path: Path) -> None:
    common = [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-i", video_path, "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
    ]
    tail = ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(out_path)]
    # We only swap the audio, so copy the video stream (fast, avoids libx264
    # failures). Fall back to a re-encode only if copy can't be muxed.
    attempts = [
        [*common, "-c:v", "copy", *tail],
        [*common, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", FFMPEG_THREADS,
         "-pix_fmt", "yuv420p", *tail],
    ]
    last_err = ""
    for cmd in attempts:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return
        last_err = (proc.stderr or "").strip()
    raise RuntimeError(f"ffmpeg mux failed: {last_err[-600:]}")
