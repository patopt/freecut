"""TikTok OAuth + Content Posting API (stdlib only, no extra dependency).

Two posting modes:
  * direct  -> /v2/post/publish/video/init/        (scope video.publish)
  * inbox   -> /v2/post/publish/inbox/video/init/  (scope video.upload)

Unaudited API clients can only publish privately (SELF_ONLY); inbox mode drops
the video into the creator's drafts instead, which needs no audit.
Video bytes are sent with the FILE_UPLOAD source in a single chunk when the
file is small enough, otherwise in 5-64 MB chunks.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import db

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES_DIRECT = "user.info.basic,video.publish,video.upload"
SCOPES_INBOX = "user.info.basic,video.upload"

CHUNK = 60 * 1024 * 1024        # 60 MB, inside TikTok's 5-64 MB window
SINGLE_MAX = 64 * 1024 * 1024   # send as one chunk below this

PRIVACY = {
    "public": "PUBLIC_TO_EVERYONE",
    "friends": "MUTUAL_FOLLOW_FRIENDS",
    "followers": "FOLLOWER_OF_CREATOR",
    "private": "SELF_ONLY",
}


def _post_json(url: str, payload: dict, token: str | None = None,
               extra_headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json; charset=UTF-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _check(resp: dict) -> dict:
    err = (resp or {}).get("error") or {}
    code = err.get("code")
    if code and code != "ok":
        raise RuntimeError(f"TikTok API error: {code} — {err.get('message', '')}")
    return resp.get("data", resp)


# --- OAuth ------------------------------------------------------------------

def build_auth_url(client_key: str, redirect_uri: str, state: str, mode: str = "direct") -> str:
    scopes = SCOPES_DIRECT if mode == "direct" else SCOPES_INBOX
    q = urllib.parse.urlencode({
        "client_key": client_key,
        "scope": scopes,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"{AUTH_URL}?{q}"


def exchange_code(client_key: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    resp = _post_form(f"{API}/oauth/token/", {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": urllib.parse.unquote(code),
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    if resp.get("error"):
        raise RuntimeError(f"TikTok token error: {resp.get('error_description') or resp['error']}")
    resp["obtained_at"] = time.time()
    return resp


def _refresh(client_key: str, client_secret: str, token: dict) -> dict:
    resp = _post_form(f"{API}/oauth/token/", {
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": token.get("refresh_token", ""),
    })
    if resp.get("error"):
        raise RuntimeError(f"TikTok refresh failed: {resp.get('error_description')}")
    resp["obtained_at"] = time.time()
    return resp


def access_token(account: dict) -> str:
    """Return a valid access token, refreshing and persisting it if needed."""
    token = json.loads(account.get("token") or "{}")
    expires_in = float(token.get("expires_in", 0) or 0)
    obtained = float(token.get("obtained_at", 0) or 0)
    if not token.get("access_token"):
        raise RuntimeError("TikTok account not connected")
    # Refresh a few minutes before expiry.
    if obtained and expires_in and time.time() > obtained + expires_in - 300:
        token = _refresh(db.effective("tiktok_client_key"),
                         db.effective("tiktok_client_secret"), token)
        db.update_tiktok_token(account["id"], json.dumps(token))
    return token["access_token"]


def fetch_user(token: str) -> dict:
    url = f"{API}/user/info/?fields=open_id,display_name,avatar_url"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = _check(json.loads(resp.read().decode()))
    user = data.get("user", {})
    return {"open_id": user.get("open_id", ""), "display_name": user.get("display_name", "TikTok"),
            "avatar": user.get("avatar_url", "")}


def creator_info(token: str) -> dict:
    """Required before a direct post; also tells us the allowed privacy levels."""
    resp = _post_json(f"{API}/post/publish/creator_info/query/", {}, token)
    return _check(resp)


# --- posting ----------------------------------------------------------------

def _upload_chunks(upload_url: str, path: Path, size: int, chunk_size: int) -> None:
    with open(path, "rb") as f:
        start = 0
        while start < size:
            blob = f.read(chunk_size)
            if not blob:
                break
            end = start + len(blob) - 1
            req = urllib.request.Request(upload_url, data=blob, method="PUT", headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(blob)),
                "Content-Range": f"bytes {start}-{end}/{size}",
            })
            with urllib.request.urlopen(req, timeout=600):
                pass
            start = end + 1


def post_video(account: dict, video_path: str, title: str, *,
               mode: str = "direct", privacy: str = "private") -> str:
    """Upload a video. Returns the TikTok publish_id."""
    token = access_token(account)
    path = Path(video_path)
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError("Video file is empty")

    chunk_size = size if size <= SINGLE_MAX else CHUNK
    total_chunks = 1 if size <= SINGLE_MAX else (size + chunk_size - 1) // chunk_size
    source_info = {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": chunk_size,
        "total_chunk_count": total_chunks,
    }

    if mode == "direct":
        info = creator_info(token)
        allowed = info.get("privacy_level_options") or ["SELF_ONLY"]
        wanted = PRIVACY.get(privacy, "SELF_ONLY")
        if wanted not in allowed:
            wanted = "SELF_ONLY" if "SELF_ONLY" in allowed else allowed[0]
        payload = {
            "post_info": {
                "title": (title or "")[:2200],
                "privacy_level": wanted,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": source_info,
        }
        endpoint = f"{API}/post/publish/video/init/"
    else:
        payload = {"source_info": source_info}
        endpoint = f"{API}/post/publish/inbox/video/init/"

    data = _check(_post_json(endpoint, payload, token))
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id", "")
    if not upload_url:
        raise RuntimeError("TikTok did not return an upload URL")
    _upload_chunks(upload_url, path, size, chunk_size)
    return publish_id


def publish_status(account: dict, publish_id: str) -> dict:
    token = access_token(account)
    resp = _post_json(f"{API}/post/publish/status/fetch/", {"publish_id": publish_id}, token)
    return _check(resp)
