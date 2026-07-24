"""Publish to TikTok by driving a real browser on the VPS (no API, no audit).

Authentication uses **session cookies** you export from a browser where you are
already logged in (the same "Get cookies.txt LOCALLY" extension used for
yt-dlp). We never store your TikTok password: that avoids the login captcha /
2FA wall entirely and keeps credentials off the server.

Note: automated posting is against TikTok's Terms of Service and can get an
account restricted. Use it on accounts you own and keep the volume human.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .. import config

Log = Callable[[str], None]
UPLOAD_URLS = [
    "https://www.tiktok.com/tiktokstudio/upload",
    "https://www.tiktok.com/upload?lang=en",
]


def cookies_dir() -> Path:
    d = config.DATA_DIR / "tiktok_cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_netscape_cookies(text: str) -> list[dict]:
    """Convert a Netscape cookies.txt export into Playwright cookie dicts."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        try:
            expiry = int(float(expires))
        except ValueError:
            expiry = 0
        cookie = {
            "name": name, "value": value, "domain": domain, "path": path or "/",
            "httpOnly": False, "secure": secure.upper() == "TRUE",
            "sameSite": "Lax",
        }
        if expiry > 0:
            cookie["expires"] = expiry
        out.append(cookie)
    return out


def save_cookies(account_id: str, cookies_text: str) -> tuple[Path, int]:
    cookies = parse_netscape_cookies(cookies_text)
    if not cookies:
        # Maybe the user pasted a Playwright/JSON export already.
        try:
            data = json.loads(cookies_text)
            cookies = data.get("cookies", data) if isinstance(data, (dict, list)) else []
        except json.JSONDecodeError:
            cookies = []
    tiktok = [c for c in cookies if "tiktok" in (c.get("domain") or "")]
    if not tiktok:
        raise RuntimeError("No TikTok cookies found in that file.")
    path = cookies_dir() / f"{account_id}.json"
    path.write_text(json.dumps({"cookies": tiktok, "origins": []}), encoding="utf-8")
    return path, len(tiktok)


def _find_and_upload(page, video_path: str, caption: str, log: Log) -> None:
    # The upload page renders the file input inside an iframe on some variants.
    frames = [page] + list(page.frames)
    file_input = None
    for fr in frames:
        try:
            el = fr.query_selector("input[type='file']")
        except Exception:  # noqa: BLE001
            el = None
        if el:
            file_input, host = el, fr
            break
    if not file_input:
        raise RuntimeError("Upload page did not expose a file input (layout changed or not logged in)")
    file_input.set_input_files(video_path)
    log("Video file attached, waiting for processing…")

    # Caption box: TikTok uses a contenteditable DraftJS editor.
    caption_selectors = [
        "div[contenteditable='true']",
        "div.public-DraftEditor-content",
        "[data-e2e='video-caption'] div[contenteditable='true']",
    ]
    deadline = time.time() + 180
    box = None
    while time.time() < deadline and box is None:
        for sel in caption_selectors:
            try:
                box = host.query_selector(sel)
            except Exception:  # noqa: BLE001
                box = None
            if box:
                break
        if box is None:
            page.wait_for_timeout(2000)
    if box and caption:
        try:
            box.click()
            # Clear whatever TikTok prefilled (usually the filename).
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            box.type(caption[:2100], delay=8)
            log("Caption filled")
        except Exception as exc:  # noqa: BLE001
            log(f"Could not set caption ({exc}); posting without it")

    # Wait until the Post button is enabled (upload finished), then click it.
    post_selectors = [
        "button[data-e2e='post_video_button']",
        "button:has-text('Post')",
        "button:has-text('Publier')",
    ]
    deadline = time.time() + 900
    while time.time() < deadline:
        for sel in post_selectors:
            try:
                btn = host.query_selector(sel)
            except Exception:  # noqa: BLE001
                btn = None
            if btn and btn.is_enabled():
                btn.click()
                log("Post button clicked")
                page.wait_for_timeout(8000)
                return
        page.wait_for_timeout(3000)
    raise RuntimeError("Post button never became clickable (upload stuck or session expired)")


def post_video(account: dict, video_path: str, caption: str, log: Log = lambda _m: None) -> str:
    """Upload with a real browser.

    Preferred path: reuse the account's **persistent profile** (created when you
    logged in through the dashboard's remote browser), so TikTok sees the same
    device/session as your manual login. Falls back to an imported cookies.txt.
    """
    from playwright.sync_api import sync_playwright

    from . import tiktok_session

    profile = tiktok_session.profiles_dir() / account["id"]
    use_profile = profile.exists() and any(profile.iterdir())
    state_path = cookies_dir() / f"{account['id']}.json"
    if not use_profile and not state_path.exists():
        raise RuntimeError(
            "This TikTok account has no session. Open Settings → Connect a TikTok "
            "account and log in through the remote browser.")

    shots = config.DATA_DIR / "tiktok_debug"
    shots.mkdir(parents=True, exist_ok=True)
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"]

    with sync_playwright() as pw:
        browser = None
        if use_profile:
            log("Using the account's saved browser profile")
            ctx = pw.chromium.launch_persistent_context(
                str(profile), headless=True, args=args,
                viewport={"width": 1280, "height": 900}, locale="en-US")
        else:
            log("Using imported cookies.txt")
            browser = pw.chromium.launch(headless=True, args=args)
            ctx = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": 1400, "height": 1000},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
                locale="en-US",
            )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            last_err = None
            for url in UPLOAD_URLS:
                try:
                    log(f"Opening {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(5000)
                    if "login" in page.url:
                        raise RuntimeError("Redirected to login — cookies expired, export them again")
                    _find_and_upload(page, video_path, caption, log)
                    if not use_profile:
                        # Persist any refreshed cookies for next time.
                        ctx.storage_state(path=str(state_path))
                    return f"browser-{int(time.time())}"
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    log(f"Attempt failed on {url}: {exc}")
            shot = shots / f"fail_{int(time.time())}.png"
            try:
                page.screenshot(path=str(shot), full_page=True)
                log(f"Debug screenshot: {shot}")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(str(last_err) if last_err else "TikTok upload failed")
        finally:
            ctx.close()
            if browser is not None:
                browser.close()
