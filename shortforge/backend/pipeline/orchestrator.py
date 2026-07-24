"""Run a full job end-to-end, reporting progress into the DB as it goes."""

from __future__ import annotations

from pathlib import Path

from .. import config, db
from . import audio_mix, captions, download, highlights, reframe, render, transcribe


def _band(lo: int, hi: int):
    """Return a callback that maps a 0..1 sub-progress into the [lo, hi] band."""
    def cb(frac: float, message: str, job_id: str) -> None:
        pct = int(lo + (hi - lo) * max(0.0, min(1.0, frac)))
        db.update_job(job_id, progress=pct, message=message)
    return cb


def run_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        return
    params = job.get("params", {})
    count = int(params.get("count", 6))
    want_captions = bool(params.get("captions", True))
    reframe_mode = params.get("reframe", "face")
    min_len = float(params.get("min_len", 15))
    max_len = float(params.get("max_len", 60))

    try:
        # 1. Download -------------------------------------------------------
        db.update_job(job_id, status="downloading", stage="Downloading", progress=1)
        db.append_log(job_id, f"Fetching source: {job['url']}")
        dl_cb = _band(1, 15)
        src = download.download_source(
            job["url"], job_id, lambda f, m: dl_cb(f, m, job_id)
        )
        db.update_job(job_id, source_path=src["path"], title=src["title"],
                      duration=src["duration"])
        db.append_log(job_id, f"Downloaded '{src['title']}' ({src['duration']:.0f}s)")

        # 2. Transcribe -----------------------------------------------------
        db.update_job(job_id, status="transcribing", stage="Transcribing", progress=15)
        whisper_model = db.effective("whisper_model") or "small"
        db.append_log(job_id, f"Transcribing with whisper '{whisper_model}'")
        tr_cb = _band(15, 45)
        tr = transcribe.transcribe(
            src["path"], whisper_model, src["duration"],
            lambda f, m: tr_cb(f, m, job_id),
        )
        db.append_log(job_id, f"Transcript: {len(tr.segments)} segments, lang={tr.language}")

        # 3. Highlights (LLM) ----------------------------------------------
        db.update_job(job_id, status="analyzing", stage="Finding highlights", progress=46)
        clips, note = highlights.find_highlights(
            tr,
            api_key=db.effective("gemini_api_key"),
            model=db.effective("gemini_model") or "gemini-2.5-pro",
            count=count, min_len=min_len, max_len=max_len,
        )
        if note:
            db.append_log(job_id, note)
        if not clips:
            raise RuntimeError("No highlights could be selected from this video.")
        db.append_log(job_id, f"Selected {len(clips)} highlights")
        db.update_job(job_id, progress=55, num_shorts=len(clips))

        # 4. Render each short ---------------------------------------------
        db.update_job(job_id, status="rendering", stage="Rendering shorts")
        src_w, src_h = reframe.probe_dimensions(src["path"])
        out_dir = config.OUTPUT_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        for i, clip in enumerate(clips):
            n = i + 1
            db.append_log(job_id, f"Rendering short {n}/{len(clips)}: {clip['title']}")

            if reframe_mode == "face":
                cx = reframe.detect_face_center(src["path"], clip["start"], clip["end"])
            else:
                cx = 0.5
            crop = reframe.compute_crop(src_w, src_h, cx)

            ass_path = None
            if want_captions:
                words = tr.words_between(clip["start"], clip["end"])
                ass_path = captions.build_ass(
                    words, clip["start"], out_dir / f"short_{n:02d}.ass"
                )

            out_file = out_dir / f"short_{n:02d}.mp4"
            thumb_file = out_dir / f"short_{n:02d}.jpg"
            result = render.render_short(
                src["path"], clip["start"], clip["end"], crop,
                ass_path, out_file, thumb_file,
            )

            # Optional background music.
            music_id = params.get("music_id")
            if music_id:
                track = db.get_music(music_id)
                if track and track.get("path"):
                    try:
                        audio_mix.apply_music_in_place(
                            str(out_file), track["path"],
                            gain=float(params.get("music_gain", 0.18)),
                        )
                    except Exception as exc:  # noqa: BLE001
                        db.append_log(job_id, f"Music mix skipped: {exc}")

            db.add_short(
                job_id, idx=n, title=clip["title"], reason=clip["reason"],
                score=clip["score"], start=clip["start"], end=clip["end"],
                path=result["path"], thumb=result["thumb"],
                width=result["width"], height=result["height"],
            )
            pct = 55 + int(45 * n / len(clips))
            db.update_job(job_id, progress=pct, message=f"Rendered {n}/{len(clips)}")

        db.update_job(job_id, status="done", stage="Done", progress=100,
                      message=f"{len(clips)} shorts ready")
        db.append_log(job_id, "All shorts rendered.")

    except Exception as exc:  # noqa: BLE001 — record failure, keep worker alive
        db.append_log(job_id, f"ERROR: {exc}")
        db.update_job(job_id, status="error", stage="Failed", error=str(exc))
