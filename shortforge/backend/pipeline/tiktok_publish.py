"""Automatic TikTok posting via the `tiktok-uploader` package.

That library duplicates a logged-in browser's cookies into a remote-controlled
Chrome, which is exactly the session we already capture through the dashboard's
noVNC login. We therefore reuse those cookies — no extra login step — and hand
them to `upload_video(..., cookies_list=[...])`.

This is the preferred automatic path; the in-house Playwright uploader stays as
a fallback for when this one can't run.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .. import config

Log = Callable[[str], None]


# Budgets are nested: uploader (10m) + fallback (8m) = 18m, which stays under
# the publish watchdog (20m) so the two can't fight over the same row.
UPLOAD_TIMEOUT = 10 * 60
FALLBACK_TIMEOUT = 8 * 60


def run_without_event_loop(fn: Callable[[], object], timeout: float = UPLOAD_TIMEOUT) -> object:
    """Run `fn` on a fresh thread that provably has no running asyncio loop.

    tiktok-uploader (and our fallback) use Playwright's **sync** API, which
    refuses to start when asyncio.get_running_loop() succeeds — the
    "Playwright Sync API inside the asyncio loop" error. A brand-new thread
    never has a running loop, so this makes the call safe from anywhere.

    The join is bounded: a browser that hangs must not freeze the publish loop
    forever (that is what left videos stuck in "publishing").
    """
    box: dict[str, object] = {}

    def runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            box["error"] = exc

    thread = threading.Thread(target=runner, name="playwright-sync", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(
            f"The browser upload did not finish within {int(timeout / 60)} minutes. "
            "It was abandoned; press Retry to try again.")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def _storage_state_path(account_id: str) -> Path:
    return config.DATA_DIR / "tiktok_cookies" / f"{account_id}.json"


def build_cookies_list(account_id: str) -> list[dict]:
    """Convert the saved browser session into tiktok-uploader's cookie format.

    Keys must be name/value/domain/path (+ optional expiry). Selenium rejects a
    negative expiry, so session cookies simply omit it.
    """
    path = _storage_state_path(account_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    raw = data.get("cookies", data) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for c in raw:
        name = c.get("name")
        domain = c.get("domain") or ""
        if not name or "tiktok" not in domain:
            continue
        cookie = {
            "name": name,
            "value": c.get("value", ""),
            "domain": domain,
            "path": c.get("path") or "/",
        }
        expiry = c.get("expiry", c.get("expires"))
        try:
            expiry = int(float(expiry)) if expiry is not None else 0
        except (TypeError, ValueError):
            expiry = 0
        if expiry > int(time.time()):
            cookie["expiry"] = expiry
        out.append(cookie)
    return out


def has_session(account_id: str) -> bool:
    return bool(build_cookies_list(account_id))


def _missing_browser() -> str:
    """Return an explanatory message if Playwright's Chromium isn't installed."""
    from pathlib import Path as _P

    roots = [_P.home() / ".cache/ms-playwright"]
    import os as _os

    env_root = _os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_root:
        roots.insert(0, _P(env_root))
    for root in roots:
        if root.is_dir() and any(root.glob("chromium*/chrome-linux/chrome")):
            return ""
    return ("Playwright's Chromium is not installed. Run on the VPS: "
            "source venv/bin/activate && python -m playwright install chromium")


def available() -> bool:
    try:
        import tiktok_uploader  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def post_video(account: dict, video_path: str, description: str,
               log: Log = lambda _m: None, schedule=None) -> str:
    """Upload with tiktok-uploader. Raises with a readable reason on failure."""
    from tiktok_uploader.upload import upload_video

    cookies = build_cookies_list(account["id"])
    if not cookies:
        raise RuntimeError(
            "No saved TikTok session for this account. Connect it again from "
            "Settings (remote browser login).")
    if not Path(video_path).exists():
        raise RuntimeError(f"Video file is missing: {video_path}")

    # Fail fast with a clear message if the browser was never installed —
    # otherwise Playwright can sit there trying to resolve it.
    missing = _missing_browser()
    if missing:
        raise RuntimeError(missing)

    log(f"Uploading with tiktok-uploader ({len(cookies)} cookies)…")
    kwargs = {
        "filename": str(video_path),
        "description": (description or "")[:2150],
        "cookies_list": cookies,
        "browser": "chrome",
        "headless": True,
    }
    if schedule is not None:
        kwargs["schedule"] = schedule

    # Sync Playwright must not see a running asyncio loop — isolate the call.
    failed = run_without_event_loop(lambda: upload_video(**kwargs))
    # The library returns the list of videos it could NOT upload.
    if failed:
        raise RuntimeError(
            "tiktok-uploader could not post the video (session may be expired — "
            "reconnect the account from Settings).")
    log("tiktok-uploader reported success")
    return f"ttu-{int(time.time())}"


def try_post(account: dict, video_path: str, description: str,
             log: Log = lambda _m: None) -> Optional[str]:
    """Attempt the upload; return None so the caller can fall back."""
    if not available():
        log("tiktok-uploader is not installed — falling back")
        return None
    if not has_session(account["id"]):
        log("No stored cookies for tiktok-uploader — falling back")
        return None
    try:
        return post_video(account, video_path, description, log)
    except Exception as exc:  # noqa: BLE001
        log(f"tiktok-uploader failed: {exc}")
        return None
