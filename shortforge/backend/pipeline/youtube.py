"""Google/YouTube OAuth + Data API helpers.

All google-* imports are lazy so the app boots without the libraries installed.
Tokens are stored per Google account as the JSON produced by
Credentials.to_json(); they auto-refresh and are persisted back on use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .. import db

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def _client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_auth_url(client_id: str, client_secret: str, redirect_uri: str, state: str) -> str:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri), scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true",
        prompt="consent", state=state)
    return url


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    """Return {email, token_json, channels:[{yt_channel_id,title,thumb}]}."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri), scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_json = creds.to_json()
    email = _fetch_email(creds)
    channels = _list_channels(creds)
    return {"email": email, "token_json": token_json, "channels": channels}


def _creds_from_account(account: dict):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    info = json.loads(account["token"])
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        db.update_google_token(account["id"], creds.to_json())
    return creds


def _service(account: dict, api: str = "youtube", version: str = "v3"):
    from googleapiclient.discovery import build

    creds = _creds_from_account(account)
    return build(api, version, credentials=creds, cache_discovery=False)


def _fetch_email(creds) -> str:
    try:
        from googleapiclient.discovery import build

        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return svc.userinfo().get().execute().get("email", "")
    except Exception:
        return ""


def _list_channels(creds) -> list[dict]:
    from googleapiclient.discovery import build

    svc = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = svc.channels().list(part="snippet", mine=True, maxResults=50).execute()
    out = []
    for item in resp.get("items", []):
        sn = item.get("snippet", {})
        thumb = sn.get("thumbnails", {}).get("default", {}).get("url", "")
        out.append({"yt_channel_id": item["id"], "title": sn.get("title", "Channel"), "thumb": thumb})
    return out


def upload_video(account: dict, video_path: str, title: str, description: str,
                 tags: list[str], privacy: str = "public") -> str:
    from googleapiclient.http import MediaFileUpload

    svc = _service(account)
    body = {
        "snippet": {
            "title": (title or "Short")[:100],
            "description": (description or "")[:4900],
            "tags": [t for t in tags if t][:30],
            "categoryId": "24",  # Entertainment
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = svc.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _status, response = request.next_chunk()
    return response["id"]


def video_stats(account: dict, video_ids: list[str]) -> dict:
    """Return {yt_video_id: viewCount(int)} for the given ids."""
    if not video_ids:
        return {}
    svc = _service(account)
    out: dict[str, int] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        resp = svc.videos().list(part="statistics", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            out[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0))
    return out
