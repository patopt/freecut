"""ShortForge FastAPI app: auth, job API, live progress (SSE), static dashboard."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, worker
from .pipeline import channels as channels_mod
from .pipeline import translate as translate_mod

app = FastAPI(title="ShortForge")


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    db.init_db()
    db.seed_defaults()
    auth.ensure_password_seeded()
    worker.start_worker()


@app.on_event("shutdown")
def _shutdown() -> None:
    worker.stop_worker()


# --- auth helpers -----------------------------------------------------------

def require_auth(request: Request) -> None:
    if not auth.valid_session(request.cookies.get(auth.COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Not authenticated")


# --- pages ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not auth.valid_session(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/login")
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return FileResponse(config.FRONTEND_DIR / "login.html")


# --- auth API ---------------------------------------------------------------

@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    if not auth.verify_password(str(body.get("password", ""))):
        raise HTTPException(status_code=401, detail="Wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.COOKIE_NAME, auth.issue_session(),
        httponly=True, samesite="lax", max_age=auth.MAX_AGE,
    )
    return resp


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.get("/api/me")
def api_me(_: None = Depends(require_auth)):
    return {"ok": True}


# --- settings ---------------------------------------------------------------

@app.get("/api/settings")
def get_settings(_: None = Depends(require_auth)):
    return {
        "gemini_api_key_set": bool(db.effective("gemini_api_key")),
        "gemini_model": db.effective("gemini_model"),
        "whisper_model": db.effective("whisper_model"),
        "tts_engine": db.effective("tts_engine"),
        "ngrok_authtoken_set": bool(db.effective("ngrok_authtoken")),
    }


@app.post("/api/settings")
async def update_settings(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    # Only overwrite secrets when a non-empty value is provided.
    for key in ("gemini_api_key", "ngrok_authtoken"):
        val = str(body.get(key, "")).strip()
        if val:
            db.set_setting(key, val)
    for key in ("gemini_model", "whisper_model", "tts_engine"):
        val = str(body.get(key, "")).strip()
        if val:
            db.set_setting(key, val)
    new_pw = str(body.get("new_password", "")).strip()
    if new_pw:
        auth.set_password(new_pw)
    return {"ok": True}


# --- jobs -------------------------------------------------------------------

@app.post("/api/jobs")
async def create_job(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
    params = {
        "count": max(1, min(20, int(body.get("count", 6)))),
        "captions": bool(body.get("captions", True)),
        "reframe": "face" if body.get("reframe", "face") == "face" else "center",
        "min_len": max(5, min(90, float(body.get("min_len", 15)))),
        "max_len": max(10, min(180, float(body.get("max_len", 60)))),
    }
    jid = db.create_job(url, "", params)
    return {"id": jid}


def _job_payload(job: dict) -> dict:
    return {**job, "shorts": db.list_shorts(job["id"])}


@app.get("/api/jobs")
def list_jobs(_: None = Depends(require_auth)):
    return {"jobs": db.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(require_auth)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Not found")
    return _job_payload(job)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, _: None = Depends(require_auth)):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Not found")
    shutil.rmtree(config.OUTPUT_DIR / job_id, ignore_errors=True)
    shutil.rmtree(config.WORK_DIR / job_id, ignore_errors=True)
    db.delete_job(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, _: None = Depends(require_auth)):
    terminal = {"done", "error"}

    async def gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            job = db.get_job(job_id)
            if not job:
                yield "event: gone\ndata: {}\n\n"
                break
            payload = json.dumps(_job_payload(job))
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if job["status"] in terminal:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- shorts / media ---------------------------------------------------------

def _short_or_404(short_id: str) -> dict:
    short = db.get_short(short_id)
    if not short or not short.get("path") or not Path(short["path"]).exists():
        raise HTTPException(status_code=404, detail="Not found")
    return short


@app.get("/api/shorts/{short_id}/video")
def short_video(short_id: str, request: Request, _: None = Depends(require_auth)):
    short = _short_or_404(short_id)
    return FileResponse(short["path"], media_type="video/mp4")


@app.get("/api/shorts/{short_id}/download")
def short_download(short_id: str, _: None = Depends(require_auth)):
    short = _short_or_404(short_id)
    name = f"short_{short['idx']:02d}.mp4"
    return FileResponse(short["path"], media_type="video/mp4", filename=name)


@app.get("/api/shorts/{short_id}/thumb")
def short_thumb(short_id: str, _: None = Depends(require_auth)):
    short = db.get_short(short_id)
    if not short or not short.get("thumb") or not Path(short["thumb"]).exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(short["thumb"], media_type="image/jpeg")


# --- COPY mode: channels ----------------------------------------------------

def _refresh_channel_bg(channel_id: str) -> None:
    threading.Thread(
        target=channels_mod.refresh_channel, args=(channel_id,), daemon=True
    ).start()


@app.get("/api/languages")
def languages(_: None = Depends(require_auth)):
    return {"languages": translate_mod.LANGUAGE_NAMES}


@app.post("/api/channels")
async def add_channel(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing channel URL")
    cid = db.create_channel(url)
    _refresh_channel_bg(cid)
    return {"id": cid}


@app.get("/api/channels")
def get_channels(_: None = Depends(require_auth)):
    return {"channels": db.list_channels()}


@app.get("/api/channels/{channel_id}")
def channel_detail(channel_id: str, _: None = Depends(require_auth)):
    ch = db.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Not found")
    return {**ch, "shorts": db.list_channel_shorts(channel_id)}


@app.post("/api/channels/{channel_id}/refresh")
def refresh_channel(channel_id: str, _: None = Depends(require_auth)):
    if not db.get_channel(channel_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.update_channel(channel_id, status="fetching", message="Refreshing…")
    _refresh_channel_bg(channel_id)
    return {"ok": True}


@app.delete("/api/channels/{channel_id}")
def delete_channel(channel_id: str, _: None = Depends(require_auth)):
    if not db.get_channel(channel_id):
        raise HTTPException(status_code=404, detail="Not found")
    # Remove dub output files for this channel's shorts.
    for short in db.list_channel_shorts(channel_id):
        for d in short.get("dubs", []):
            row = db.get_dub(d["id"])
            if row and row.get("path"):
                Path(row["path"]).unlink(missing_ok=True)
            if row and row.get("thumb"):
                Path(row["thumb"]).unlink(missing_ok=True)
    db.delete_channel(channel_id)
    return {"ok": True}


# --- COPY mode: dubs --------------------------------------------------------

@app.post("/api/shorts-src/{short_id}/dub")
async def create_dub(short_id: str, request: Request, _: None = Depends(require_auth)):
    if not db.get_channel_short(short_id):
        raise HTTPException(status_code=404, detail="Short not found")
    body = await request.json()
    lang = str(body.get("lang", "")).strip()
    if lang not in translate_mod.LANGUAGE_NAMES:
        raise HTTPException(status_code=400, detail="Unsupported language")
    dest = str(body.get("dest_channel_id", "")).strip()
    if dest and not db.get_my_channel(dest):
        raise HTTPException(status_code=400, detail="Unknown destination channel")
    did = db.create_dub(short_id, lang, dest)
    return {"id": did}


# --- COPY mode: my channels (dubbing destinations) --------------------------

@app.post("/api/my-channels")
async def add_my_channel(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Missing name")
    return {"id": db.create_my_channel(name)}


@app.get("/api/my-channels")
def get_my_channels(_: None = Depends(require_auth)):
    return {"channels": db.list_my_channels()}


@app.get("/api/my-channels/{channel_id}")
def my_channel_detail(channel_id: str, _: None = Depends(require_auth)):
    ch = db.get_my_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Not found")
    return {**ch, "dubs": db.list_dubs_for_dest(channel_id)}


@app.delete("/api/my-channels/{channel_id}")
def delete_my_channel(channel_id: str, _: None = Depends(require_auth)):
    if not db.get_my_channel(channel_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.delete_my_channel(channel_id)
    return {"ok": True}


@app.get("/api/dubs/{dub_id}")
def get_dub(dub_id: str, _: None = Depends(require_auth)):
    d = db.get_dub(dub_id)
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    return d


@app.get("/api/dubs/{dub_id}/events")
async def dub_events(dub_id: str, request: Request, _: None = Depends(require_auth)):
    terminal = {"done", "error"}

    async def gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            d = db.get_dub(dub_id)
            if not d:
                yield "event: gone\ndata: {}\n\n"
                break
            payload = json.dumps(d)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if d["status"] in terminal:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _dub_file_or_404(dub_id: str) -> dict:
    d = db.get_dub(dub_id)
    if not d or not d.get("path") or not Path(d["path"]).exists():
        raise HTTPException(status_code=404, detail="Not found")
    return d


@app.get("/api/dubs/{dub_id}/video")
def dub_video(dub_id: str, _: None = Depends(require_auth)):
    d = _dub_file_or_404(dub_id)
    return FileResponse(d["path"], media_type="video/mp4")


@app.get("/api/dubs/{dub_id}/download")
def dub_download(dub_id: str, _: None = Depends(require_auth)):
    d = _dub_file_or_404(dub_id)
    return FileResponse(d["path"], media_type="video/mp4", filename=f"dub_{d['lang']}_{dub_id}.mp4")


@app.get("/api/dubs/{dub_id}/thumb")
def dub_thumb(dub_id: str, _: None = Depends(require_auth)):
    d = db.get_dub(dub_id)
    if not d or not d.get("thumb") or not Path(d["thumb"]).exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(d["thumb"], media_type="image/jpeg")


# Static assets (css/js) — safe to serve without auth (no secrets).
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
