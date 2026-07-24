"""ShortForge FastAPI app: auth, job API, live progress (SSE), static dashboard."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile,
    WebSocket,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, worker
from .pipeline import autopublish as autopublish_mod
from .pipeline import captions as captions_mod
from .pipeline import channels as channels_mod
from .pipeline import tiktok as tiktok_mod
from .pipeline import tiktok_browser as tiktok_browser_mod
from .pipeline import tiktok_session as tiktok_session_mod
from .pipeline import translate as translate_mod
from .pipeline import youtube as youtube_mod

app = FastAPI(title="ShortForge")


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    db.init_db()
    db.seed_defaults()
    auth.ensure_password_seeded()
    worker.start_worker()
    autopublish_mod.start_scheduler()


@app.on_event("shutdown")
def _shutdown() -> None:
    worker.stop_worker()
    autopublish_mod.stop_scheduler()
    tiktok_session_mod.shutdown()


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
        "default_dub_music": db.effective("default_dub_music"),
        "default_caption_style": db.effective("default_caption_style") or captions_mod.DEFAULT_PRESET,
        "default_dub_captions": db.effective("default_dub_captions"),
        "google_client_id_set": bool(db.effective("google_client_id")),
        "google_client_secret_set": bool(db.effective("google_client_secret")),
        "tiktok_client_key_set": bool(db.effective("tiktok_client_key")),
        "tiktok_client_secret_set": bool(db.effective("tiktok_client_secret")),
    }


@app.post("/api/settings")
async def update_settings(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    # Only overwrite secrets when a non-empty value is provided.
    for key in ("gemini_api_key", "ngrok_authtoken", "google_client_id", "google_client_secret",
                "tiktok_client_key", "tiktok_client_secret"):
        val = str(body.get(key, "")).strip()
        if val:
            db.set_setting(key, val)
    for key in ("gemini_model", "whisper_model", "tts_engine"):
        val = str(body.get(key, "")).strip()
        if val:
            db.set_setting(key, val)
    # These may be intentionally cleared (empty = none).
    for key in ("default_dub_music", "public_base_url", "default_caption_style",
                "default_dub_captions"):
        if key in body:
            db.set_setting(key, str(body.get(key, "")).strip())
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
        "music_id": str(body.get("music_id", "")).strip(),
        "music_gain": max(0.0, min(1.0, float(body.get("music_gain", 0.18)))),
        "caption_style": str(body.get("caption_style", "")).strip()
        or db.effective("default_caption_style") or captions_mod.DEFAULT_PRESET,
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


@app.get("/api/caption-styles")
def caption_styles(_: None = Depends(require_auth)):
    return {"styles": captions_mod.list_presets(), "default": captions_mod.DEFAULT_PRESET}


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
    dest_platform = ""
    if dest:
        if db.get_my_channel(dest):
            dest_platform = ""
        elif db.get_youtube_channel(dest):
            dest_platform = "youtube"
        elif db.get_tiktok_account(dest):
            dest_platform = "tiktok"
        else:
            raise HTTPException(status_code=400, detail="Unknown destination channel")
    music_id = str(body.get("music_id", "")).strip() or db.effective("default_dub_music") or ""
    caption_style = str(body.get("caption_style", "")).strip()
    if caption_style == "":
        caption_style = db.effective("default_dub_captions") or ""
    did = db.create_dub(short_id, lang, dest, music_id, caption_style)
    # Sending to a connected YouTube/TikTok account = also publish it once rendered.
    if dest_platform:
        import time as _t
        db.enqueue_publish(did, dest, _t.time(), platform=dest_platform)
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
    local = [{**c, "kind": "local"} for c in db.list_my_channels()]
    yt = []
    for ch in db.list_youtube_channels():
        published = db.count_publishes(ch["id"], "published")
        pending = db.count_publishes(ch["id"], "pending") + db.count_publishes(ch["id"], "publishing")
        yt.append({
            "id": ch["id"], "name": ch["title"], "kind": "youtube",
            "thumb": ch["thumb"], "auto_enabled": ch["auto_enabled"],
            "video_count": published, "pending_count": pending,
        })
    tt = []
    for a in db.list_tiktok_accounts():
        tt.append({
            "id": a["id"], "name": a["display_name"] or "TikTok", "kind": "tiktok",
            "thumb": a["avatar"], "auto_enabled": a["auto_enabled"],
            "video_count": db.count_publishes(a["id"], "published"),
            "pending_count": db.count_publishes(a["id"], "pending")
            + db.count_publishes(a["id"], "publishing"),
        })
    return {"channels": local + yt + tt}


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


# --- management: retry / clear ---------------------------------------------

@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, _: None = Depends(require_auth)):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Not found")
    # Clear previous shorts + files, then re-queue for a clean re-run.
    shutil.rmtree(config.OUTPUT_DIR / job_id, ignore_errors=True)
    db.delete_job_shorts(job_id)
    db.reset_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/clear-failed")
def clear_failed_jobs(_: None = Depends(require_auth)):
    n = 0
    for job in db.list_failed_jobs():
        shutil.rmtree(config.OUTPUT_DIR / job["id"], ignore_errors=True)
        shutil.rmtree(config.WORK_DIR / job["id"], ignore_errors=True)
        db.delete_job(job["id"])
        n += 1
    return {"deleted": n}


@app.post("/api/dubs/{dub_id}/retry")
def retry_dub(dub_id: str, _: None = Depends(require_auth)):
    if not db.get_dub(dub_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.reset_dub(dub_id)
    return {"ok": True}


@app.delete("/api/dubs/{dub_id}")
def delete_dub(dub_id: str, _: None = Depends(require_auth)):
    d = db.get_dub(dub_id)
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    for key in ("path", "thumb"):
        if d.get(key):
            Path(d[key]).unlink(missing_ok=True)
    db.delete_dub(dub_id)
    return {"ok": True}


# --- music library ----------------------------------------------------------

@app.post("/api/music")
async def upload_music(file: UploadFile = File(...), _: None = Depends(require_auth)):
    config.ensure_dirs()
    music_dir = config.DATA_DIR / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    name = file.filename or "track.mp3"
    ext = Path(name).suffix or ".mp3"
    import uuid as _uuid
    fname = _uuid.uuid4().hex[:12] + ext
    dest = music_dir / fname
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    mid = db.add_music(name, str(dest))
    return {"id": mid, "name": name}


@app.get("/api/music")
def get_music(_: None = Depends(require_auth)):
    return {"music": [{"id": m["id"], "name": m["name"]} for m in db.list_music()]}


@app.delete("/api/music/{music_id}")
def delete_music(music_id: str, _: None = Depends(require_auth)):
    m = db.get_music(music_id)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    if m.get("path"):
        Path(m["path"]).unlink(missing_ok=True)
    db.delete_music(music_id)
    return {"ok": True}


# --- YouTube: OAuth + connected channels + auto mode ------------------------

_oauth_states: dict[str, str] = {}


def _public_base(request: Request) -> str:
    override = db.effective("public_base_url")
    if override:
        return override.rstrip("/")
    base = str(request.base_url).rstrip("/")
    # Honour the proxy (ngrok) scheme/host so the redirect URI is the public one.
    return base


@app.get("/api/youtube/config")
def youtube_config(request: Request, _: None = Depends(require_auth)):
    base = _public_base(request)
    return {
        "client_id_set": bool(db.effective("google_client_id")),
        "client_secret_set": bool(db.effective("google_client_secret")),
        "redirect_uri": f"{base}/api/youtube/callback",
        "js_origin": base,
        "public_base_url": db.effective("public_base_url"),
    }


@app.get("/api/youtube/auth-url")
def youtube_auth_url(request: Request, _: None = Depends(require_auth)):
    cid = db.effective("google_client_id")
    secret = db.effective("google_client_secret")
    if not cid or not secret:
        raise HTTPException(status_code=400, detail="Set the Google client ID/secret first")
    import secrets as _secrets
    state = _secrets.token_urlsafe(16)
    redirect_uri = f"{_public_base(request)}/api/youtube/callback"
    _oauth_states[state] = redirect_uri
    url = youtube_mod.build_auth_url(cid, secret, redirect_uri, state)
    return {"url": url}


@app.get("/api/youtube/callback")
def youtube_callback(request: Request, code: str = "", state: str = ""):
    redirect_uri = _oauth_states.pop(state, None)
    if not redirect_uri or not code:
        return RedirectResponse("/?yt=error")
    try:
        result = youtube_mod.exchange_code(
            db.effective("google_client_id"), db.effective("google_client_secret"),
            redirect_uri, code)
        account_id = db.upsert_google_account(result["email"], result["token_json"])
        for ch in result["channels"]:
            db.upsert_youtube_channel(account_id, ch["yt_channel_id"], ch["title"], ch["thumb"])
        return RedirectResponse("/?yt=connected")
    except Exception:  # noqa: BLE001
        return RedirectResponse("/?yt=error")


@app.get("/api/youtube/accounts")
def youtube_accounts(_: None = Depends(require_auth)):
    accounts = []
    for a in db.list_google_accounts():
        chans = [c for c in db.list_youtube_channels() if c["account_id"] == a["id"]]
        accounts.append({"id": a["id"], "email": a["email"],
                         "channels": [{"id": c["id"], "title": c["title"]} for c in chans]})
    return {"accounts": accounts}


@app.delete("/api/youtube/accounts/{account_id}")
def youtube_disconnect(account_id: str, _: None = Depends(require_auth)):
    if not db.get_google_account(account_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.delete_google_account(account_id)
    return {"ok": True}


@app.get("/api/youtube/channels/{channel_id}")
def youtube_channel_detail(channel_id: str, _: None = Depends(require_auth)):
    ch = db.get_youtube_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Not found")
    publishes = db.list_publishes_for_channel(channel_id)
    items = []
    published_ids = []
    for p in publishes:
        dub = db.get_dub(p["dub_id"]) or {}
        src = db.get_channel_short(dub.get("short_id", "")) if dub else None
        items.append({
            "publish_id": p["id"], "status": p["status"], "scheduled_at": p["scheduled_at"],
            "yt_video_id": p["yt_video_id"], "error": p["error"],
            "dub_id": p["dub_id"], "dub_status": dub.get("status", ""),
            "title": dub.get("tr_title") or dub.get("title") or "",
            "lang": dub.get("lang", ""),
            "source_video_id": src.get("video_id") if src else "",
        })
        if p["yt_video_id"]:
            published_ids.append(p["yt_video_id"])

    # View comparison (best-effort; needs a live token).
    account = db.get_google_account(ch["account_id"])
    views_translated, views_original = {}, {}
    if account:
        try:
            views_translated = youtube_mod.video_stats(account, published_ids)
            orig_ids = [it["source_video_id"] for it in items if it["source_video_id"]]
            views_original = youtube_mod.video_stats(account, orig_ids)
        except Exception:  # noqa: BLE001
            pass
    for it in items:
        it["views_translated"] = views_translated.get(it["yt_video_id"], None)
        it["views_original"] = views_original.get(it["source_video_id"], None)

    dubbing = db.count_publishes(channel_id, "pending")  # queued/awaiting render+publish
    return {
        "id": ch["id"], "title": ch["title"], "thumb": ch["thumb"],
        "auto_enabled": ch["auto_enabled"], "auto_config": ch["auto_config"],
        "stats": {
            "published": db.count_publishes(channel_id, "published"),
            "pending": dubbing,
            "errors": db.count_publishes(channel_id, "error"),
        },
        "items": items,
    }


@app.post("/api/youtube/channels/{channel_id}/auto")
async def youtube_set_auto(channel_id: str, request: Request, _: None = Depends(require_auth)):
    ch = db.get_youtube_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.json()
    cfg = body.get("config", {}) or {}
    enabled = 1 if body.get("enabled") else 0
    db.update_youtube_channel(channel_id, auto_enabled=enabled, auto_config=cfg)
    # Enqueue synchronously so we can report how many tasks started.
    started = 0
    if enabled:
        started = autopublish_mod.enqueue_channel(db.get_youtube_channel(channel_id))
    return {"ok": True, "started": started}


@app.post("/api/youtube/channels/{channel_id}/publish")
async def youtube_publish_now(channel_id: str, request: Request, _: None = Depends(require_auth)):
    ch = db.get_youtube_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.json()
    dub_id = str(body.get("dub_id", "")).strip()
    if not db.get_dub(dub_id):
        raise HTTPException(status_code=404, detail="Dub not found")
    import time as _t
    db.enqueue_publish(dub_id, channel_id, _t.time())
    return {"ok": True}


# --- TikTok: OAuth + connected accounts + auto mode -------------------------

_tiktok_states: dict[str, str] = {}


@app.get("/api/tiktok/config")
def tiktok_config(request: Request, _: None = Depends(require_auth)):
    base = _public_base(request)
    return {
        "client_key_set": bool(db.effective("tiktok_client_key")),
        "client_secret_set": bool(db.effective("tiktok_client_secret")),
        "redirect_uri": f"{base}/api/tiktok/callback",
        "js_origin": base,
    }


@app.get("/api/tiktok/auth-url")
def tiktok_auth_url(request: Request, mode: str = "direct", _: None = Depends(require_auth)):
    key = db.effective("tiktok_client_key")
    if not key or not db.effective("tiktok_client_secret"):
        raise HTTPException(status_code=400, detail="Set the TikTok client key/secret first")
    import secrets as _secrets
    state = _secrets.token_urlsafe(16)
    redirect_uri = f"{_public_base(request)}/api/tiktok/callback"
    _tiktok_states[state] = redirect_uri
    return {"url": tiktok_mod.build_auth_url(key, redirect_uri, state, mode)}


@app.get("/api/tiktok/callback")
def tiktok_callback(code: str = "", state: str = ""):
    redirect_uri = _tiktok_states.pop(state, None)
    if not redirect_uri or not code:
        return RedirectResponse("/?tt=error")
    try:
        token = tiktok_mod.exchange_code(
            db.effective("tiktok_client_key"), db.effective("tiktok_client_secret"),
            redirect_uri, code)
        user = tiktok_mod.fetch_user(token["access_token"])
        open_id = user["open_id"] or token.get("open_id", "")
        db.upsert_tiktok_account(open_id, user["display_name"], user["avatar"],
                                 json.dumps(token))
        return RedirectResponse("/?tt=connected")
    except Exception:  # noqa: BLE001
        return RedirectResponse("/?tt=error")


@app.get("/api/tiktok/session/status")
def tiktok_session_status(_: None = Depends(require_auth)):
    return tiktok_session_mod.status()


@app.post("/api/tiktok/session/start")
async def tiktok_session_start(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    account_id = str(body.get("account_id", "")).strip()
    if account_id:
        if not db.get_tiktok_account(account_id):
            raise HTTPException(status_code=404, detail="Account not found")
    else:
        if not name:
            raise HTTPException(status_code=400, detail="Give the account a name")
        import uuid as _uuid
        account_id = db.upsert_tiktok_account(
            f"browser-{_uuid.uuid4().hex[:10]}", name, "", "{}")
        db.update_tiktok_account(account_id, mode="browser")
    try:
        info = tiktok_session_mod.start_session(account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    return {**info, "account_id": account_id}


@app.post("/api/tiktok/session/stop")
def tiktok_session_stop(_: None = Depends(require_auth)):
    tiktok_session_mod.stop_session()
    return {"ok": True}


@app.websocket("/api/vnc/ws")
async def vnc_bridge(ws: WebSocket):
    """Bridge noVNC (WebSocket, binary RFB) to the local x11vnc TCP port."""
    if not auth.valid_session(ws.cookies.get(auth.COOKIE_NAME)):
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol="binary")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", tiktok_session_mod.VNC_PORT)
    except Exception:  # noqa: BLE001
        await ws.close(code=1011)
        return

    async def ws_to_tcp():
        try:
            while True:
                data = await ws.receive_bytes()
                writer.write(data)
                await writer.drain()
        except Exception:  # noqa: BLE001
            pass

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await ws.send_bytes(data)
        except Exception:  # noqa: BLE001
            pass

    task_a = asyncio.create_task(ws_to_tcp())
    task_b = asyncio.create_task(tcp_to_ws())
    done, pending = await asyncio.wait({task_a, task_b}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/tiktok/accounts/browser")
async def tiktok_add_browser(
    name: str = Form(...), file: UploadFile = File(...), _: None = Depends(require_auth),
):
    """Add a TikTok account driven by a headless browser using exported cookies."""
    raw = (await file.read()).decode("utf-8", errors="replace")
    import uuid as _uuid
    open_id = f"browser-{_uuid.uuid4().hex[:10]}"
    account_id = db.upsert_tiktok_account(open_id, name.strip() or "TikTok", "", "{}")
    db.update_tiktok_account(account_id, mode="browser")
    try:
        _path, n = tiktok_browser_mod.save_cookies(account_id, raw)
    except Exception as exc:  # noqa: BLE001
        db.delete_tiktok_account(account_id)
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": account_id, "cookies": n}


@app.get("/api/tiktok/accounts")
def tiktok_accounts(_: None = Depends(require_auth)):
    return {"accounts": [
        {"id": a["id"], "name": a["display_name"], "auto_enabled": a["auto_enabled"],
         "mode": a.get("mode") or "api"}
        for a in db.list_tiktok_accounts()]}


@app.delete("/api/tiktok/accounts/{account_id}")
def tiktok_disconnect(account_id: str, _: None = Depends(require_auth)):
    if not db.get_tiktok_account(account_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.delete_tiktok_account(account_id)
    return {"ok": True}


@app.get("/api/tiktok/accounts/{account_id}")
def tiktok_account_detail(account_id: str, _: None = Depends(require_auth)):
    acc = db.get_tiktok_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Not found")
    items = []
    for p in db.list_publishes_for_channel(account_id):
        dub = db.get_dub(p["dub_id"]) or {}
        items.append({
            "publish_id": p["id"], "status": p["status"], "scheduled_at": p["scheduled_at"],
            "yt_video_id": p["yt_video_id"], "error": p["error"], "dub_id": p["dub_id"],
            "dub_status": dub.get("status", ""), "lang": dub.get("lang", ""),
            "title": dub.get("tr_title") or dub.get("title") or "",
        })
    return {
        "id": acc["id"], "title": acc["display_name"], "thumb": acc["avatar"],
        "auto_enabled": acc["auto_enabled"], "auto_config": acc["auto_config"],
        "stats": {
            "published": db.count_publishes(account_id, "published"),
            "pending": db.count_publishes(account_id, "pending"),
            "errors": db.count_publishes(account_id, "error"),
        },
        "items": items,
    }


@app.post("/api/tiktok/accounts/{account_id}/auto")
async def tiktok_set_auto(account_id: str, request: Request, _: None = Depends(require_auth)):
    if not db.get_tiktok_account(account_id):
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.json()
    cfg = body.get("config", {}) or {}
    enabled = 1 if body.get("enabled") else 0
    db.update_tiktok_account(account_id, auto_enabled=enabled, auto_config=cfg)
    started = 0
    if enabled:
        started = autopublish_mod.enqueue_channel(db.get_tiktok_account(account_id), "tiktok")
    return {"ok": True, "started": started}


# --- system: 24/7 service command -------------------------------------------

@app.get("/api/activity")
def activity(kind: str = "", limit: int = 200, _: None = Depends(require_auth)):
    return {"activity": db.list_activity(min(max(limit, 1), 500), kind)}


@app.delete("/api/activity")
def clear_activity(_: None = Depends(require_auth)):
    return {"deleted": db.clear_activity()}


@app.get("/api/system/status")
def system_status(_: None = Depends(require_auth)):
    return {"paused": db.is_paused()}


@app.post("/api/system/stop-all")
def stop_all(_: None = Depends(require_auth)):
    db.set_paused(True)
    jobs = db.cancel_queued_jobs()
    dubs = db.cancel_queued_dubs()
    pubs = db.cancel_pending_publishes()
    autos = db.disable_all_auto()
    return {"paused": True, "canceled_jobs": jobs, "canceled_dubs": dubs,
            "canceled_publishes": pubs, "auto_disabled": autos}


@app.post("/api/system/resume")
def resume(_: None = Depends(require_auth)):
    db.set_paused(False)
    return {"paused": False}


@app.post("/api/system/clear-queues")
def clear_queues(_: None = Depends(require_auth)):
    """Stop and DELETE everything waiting/failed/canceled (keeps finished work)."""
    db.set_paused(True)
    db.disable_all_auto()
    job_states = ("queued", "canceled", "error")
    dub_states = ("queued", "canceled", "error")
    n_jobs = n_dubs = 0
    for job in db.jobs_by_statuses(job_states):
        shutil.rmtree(config.OUTPUT_DIR / job["id"], ignore_errors=True)
        shutil.rmtree(config.WORK_DIR / job["id"], ignore_errors=True)
        db.delete_job(job["id"])
        n_jobs += 1
    for d in db.dubs_by_statuses(dub_states):
        for key in ("path", "thumb"):
            if d.get(key):
                Path(d[key]).unlink(missing_ok=True)
        shutil.rmtree(config.WORK_DIR / f"dub_{d['id']}", ignore_errors=True)
        db.delete_dub(d["id"])
        n_dubs += 1
    n_pubs = db.delete_publishes_by_statuses(("pending", "canceled", "error"))
    return {"paused": True, "deleted_jobs": n_jobs, "deleted_dubs": n_dubs,
            "deleted_publishes": n_pubs}


@app.get("/api/system/service")
def service_command(_: None = Depends(require_auth)):
    """Return the one-time command that installs the 24/7 systemd service."""
    root = config.BASE_DIR
    return {"command": f"cd {root} && ./install-service.sh"}


# noVNC client (from the distro package) for the in-dashboard remote browser.
for _novnc in ("/usr/share/novnc", "/usr/share/webapps/novnc"):
    if Path(_novnc).is_dir():
        app.mount("/novnc", StaticFiles(directory=_novnc), name="novnc")
        break

# Static assets (css/js) — safe to serve without auth (no secrets).
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
