"""TikTok posting through makiisthenes/TiktokAutoUploader (requests-based).

That project signs and posts videos with plain HTTP calls instead of driving a
browser, which is why it is fast and doesn't hit Selenium/Playwright problems.
It authenticates from a pickled cookie file in its `CookiesDir/`.

We reuse the session captured by the dashboard's noVNC login: the cookies are
converted to its expected format and written into that directory automatically,
so connecting an account in Settings is all the user has to do.
"""

from __future__ import annotations

import json
import pickle
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .. import config

Log = Callable[[str], None]

VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "TiktokAutoUploader"
UPLOAD_TIMEOUT = 10 * 60


def vendor_dir() -> Path:
    return VENDOR_DIR


def installed() -> bool:
    return (VENDOR_DIR / "cli.py").is_file()


def cookies_dir() -> Path:
    d = VENDOR_DIR / "CookiesDir"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _storage_state(account_id: str) -> list[dict]:
    path = config.DATA_DIR / "tiktok_cookies" / f"{account_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    raw = data.get("cookies", data) if isinstance(data, dict) else data
    return raw if isinstance(raw, list) else []


def build_cookies(account_id: str) -> list[dict]:
    """Convert our saved session into the Selenium-style dicts it expects."""
    out: list[dict] = []
    for c in _storage_state(account_id):
        domain = c.get("domain") or ""
        if "tiktok" not in domain or not c.get("name"):
            continue
        cookie = {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": domain,
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
            # Its loader rewrites 'None' to 'Strict'; keep a valid value.
            "sameSite": c.get("sameSite") or "Lax",
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


def sync_cookies(account_id: str) -> int:
    """Write the account's cookies where TiktokAutoUploader looks for them.

    Its login saves sessions as ``CookiesDir/tiktok_session-<user>.cookie`` and
    `--user` is resolved against that exact prefix — writing plain
    ``<user>.cookie`` made it report "User not found on system".
    We also drop the bare name for older layouts.
    """
    cookies = build_cookies(account_id)
    if not cookies:
        return 0
    for name in (f"tiktok_session-{account_id}.cookie", f"{account_id}.cookie"):
        with open(cookies_dir() / name, "wb") as handle:
            pickle.dump(cookies, handle)
    return len(cookies)


def has_session(account_id: str) -> bool:
    return bool(build_cookies(account_id))


SIGNATURE_DIR = "tiktok_uploader/tiktok-signature"


def ready() -> str:
    """Empty string when usable, else why not."""
    if not installed():
        return ("TiktokAutoUploader is not installed. Run ./setup.sh on the VPS "
                "(it clones and prepares it).")
    if not shutil.which("node"):
        return "Node.js is not installed — TikTok uploads need it. Run ./setup.sh."
    sig = VENDOR_DIR / SIGNATURE_DIR
    if sig.is_dir() and not (sig / "node_modules").is_dir():
        return (f"The TikTok signature helper has no node_modules. Run: "
                f"cd {VENDOR_DIR / SIGNATURE_DIR} && npm install")
    return ""


def post_video(account: dict, video_path: str, title: str,
               log: Log = lambda _m: None) -> str:
    problem = ready()
    if problem:
        raise RuntimeError(problem)

    account_id = account["id"]
    cookies = build_cookies(account_id)
    if not cookies:
        raise RuntimeError(
            "No saved TikTok session for this account. Reconnect it from "
            "Settings (remote browser login).")
    # It specifically requires a `sessionid` cookie; without it the CLI says
    # "No cookie with Tiktok session id found".
    if not any(c["name"] == "sessionid" and c.get("value") for c in cookies):
        raise RuntimeError(
            "The saved session has no 'sessionid' cookie — the login did not "
            "complete. Reconnect the account from Settings and make sure you "
            "are fully logged into TikTok before pressing Done.")
    count = sync_cookies(account_id)
    video = Path(video_path).resolve()
    if not video.exists():
        raise RuntimeError(f"Video file is missing: {video}")

    # Its CLI resolves -v relative to VideosDirPath, so stage the file there and
    # pass a bare filename. This works whether or not it also accepts paths.
    videos_dir = VENDOR_DIR / "VideosDirPath"
    videos_dir.mkdir(parents=True, exist_ok=True)
    staged = videos_dir / f"{account_id}_{int(time.time())}.mp4"
    shutil.copy2(video, staged)

    log(f"Uploading via TiktokAutoUploader ({count} cookies, {staged.name})…")
    cmd = [sys.executable, "cli.py", "upload",
           "--user", account_id, "-v", staged.name, "-t", (title or "")[:2100]]
    try:
        proc = subprocess.run(cmd, cwd=str(VENDOR_DIR), capture_output=True,
                              text=True, timeout=UPLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        staged.unlink(missing_ok=True)
        raise RuntimeError(
            f"TiktokAutoUploader did not finish within {UPLOAD_TIMEOUT // 60} minutes")
    finally:
        staged.unlink(missing_ok=True)

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    tail = output[-800:] or "(no output)"
    log(f"exit={proc.returncode} :: {tail[-300:]}")
    if proc.returncode != 0:
        raise RuntimeError(f"TiktokAutoUploader failed (exit {proc.returncode}): {tail}")

    lowered = output.lower()
    # Hard failures that still exit 0 — notably the Node signature helper
    # missing its node_modules, which silently uploads nothing.
    if "module_not_found" in lowered or "cannot find module" in lowered:
        raise RuntimeError(
            "The TikTok signature helper is missing its Node packages. On the VPS run: "
            "cd vendor/TiktokAutoUploader/tiktok_uploader/tiktok-signature && npm install"
            f" — {tail}")
    for marker in ("traceback", "exception", "error:", "failed", "not found on system"):
        if marker in lowered:
            raise RuntimeError(f"TiktokAutoUploader reported an error: {tail}")

    # Require positive proof of an upload: exit code 0 alone is not enough,
    # that is how a failed run was previously recorded as "published".
    if not any(m in lowered for m in ("uploaded", "success", "posted", "published")):
        raise RuntimeError(
            f"TiktokAutoUploader finished without confirming an upload: {tail}")
    log("TiktokAutoUploader confirmed the upload")
    return f"tau-{int(time.time())}"


def try_post(account: dict, video_path: str, title: str,
             log: Log = lambda _m: None) -> Optional[str]:
    """Attempt the upload; return None so the caller can fall back."""
    try:
        return post_video(account, video_path, title, log)
    except Exception as exc:  # noqa: BLE001
        log(f"TiktokAutoUploader failed: {str(exc)[:300]}")
        return None
