"""Remote browser session on the VPS, viewable from the dashboard.

A real (headful) Chromium runs inside a virtual X display and is exposed over
VNC, so you log into TikTok from the dashboard exactly as on a PC. The browser
is driven through Playwright, which lets us watch the session live: detect the
login, capture the cookies, and persist a **dedicated profile per account** that
every later upload reuses.

Stack: Xvfb (virtual display) -> x11vnc (localhost only) -> the app's
authenticated WebSocket bridge -> noVNC in the dashboard.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .. import config, db

DISPLAY = ":99"
VNC_PORT = 5901
SCREEN = "1440x900x24"

# Per-target settings: where to land, which cookie proves a login, and which
# domain the cookies must belong to.
TARGETS = {
    "tiktok": {
        "url": "https://www.tiktok.com/login",
        "domain": "tiktok",
        "session_cookies": ("sessionid",),
        "label": "TikTok",
    },
    "youtube": {
        "url": "https://www.youtube.com/",
        "domain": "youtube",
        "session_cookies": ("SID", "__Secure-3PSID", "LOGIN_INFO"),
        "label": "YouTube",
    },
    # NordVPN doesn't use cookies: we watch for the nordvpn:// callback link the
    # site produces after sign-in and hand it to the CLI.
    "nordvpn": {
        "url": "https://nordvpn.com/",
        "domain": "nordaccount",
        "session_cookies": (),
        "label": "NordVPN",
    },
}
LOGIN_URL = TARGETS["tiktok"]["url"]
YOUTUBE_PROFILE = "youtube"

# Chromium env that silences the "Google API keys are missing" console warning.
# These are Chromium's own Safe-Browsing/Sync keys and are unrelated to Gemini.
CHROMIUM_ENV = {
    "GOOGLE_API_KEY": "no",
    "GOOGLE_DEFAULT_CLIENT_ID": "no",
    "GOOGLE_DEFAULT_CLIENT_SECRET": "no",
}

NOVNC_CANDIDATES = (
    "/usr/share/novnc",
    "/usr/share/webapps/novnc",
    "/usr/local/share/novnc",
)

_procs: dict[str, subprocess.Popen] = {}
_runner: Optional["SessionRunner"] = None
_lock = threading.RLock()


# --- paths / deps -----------------------------------------------------------

def profiles_dir() -> Path:
    d = config.DATA_DIR / "tiktok_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_path(account_id: str) -> Path:
    p = profiles_dir() / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def has_profile(account_id: str) -> bool:
    p = profiles_dir() / account_id
    return p.exists() and any(p.iterdir())


# Only one Chromium may hold a given profile at a time — the publisher and the
# remote session would otherwise fight over it.
_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_guard = threading.Lock()


def profile_lock(account_id: str) -> threading.Lock:
    with _profile_locks_guard:
        return _profile_locks.setdefault(account_id, threading.Lock())


def clean_profile_locks(profile: Path) -> None:
    """Remove stale Singleton* files left by a killed Chromium.

    Without this, Chromium aborts with "Failed to create SingletonLock: File
    exists" and refuses to open the profile ever again.
    """
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        target = profile / name
        try:
            if target.is_symlink() or target.exists():
                target.unlink()
        except Exception:  # noqa: BLE001
            pass


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def novnc_dir() -> Optional[str]:
    for d in list(NOVNC_CANDIDATES) + [str(config.DATA_DIR / "novnc")]:
        p = Path(d)
        if (p / "vnc.html").is_file() or (p / "vnc_lite.html").is_file():
            return str(p)
    return None


def novnc_page() -> str:
    d = novnc_dir()
    if d and (Path(d) / "vnc.html").is_file():
        return "vnc.html"
    return "vnc_lite.html"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def missing_deps() -> list[str]:
    missing = [c for c in ("Xvfb", "x11vnc") if not _have(c)]
    if novnc_dir() is None:
        missing.append("novnc")
    if not _chromium_available():
        missing.append("playwright")
    return missing


def _wait_for_display(timeout: float = 15.0) -> bool:
    """Block until the X display answers (or the socket appears)."""
    env = {**os.environ, "DISPLAY": DISPLAY}
    sock = Path(f"/tmp/.X11-unix/X{DISPLAY.lstrip(':')}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _have("xdpyinfo"):
            probe = subprocess.run(["xdpyinfo"], env=env, capture_output=True, check=False)
            if probe.returncode == 0:
                return True
        elif sock.exists():
            time.sleep(0.5)
            return True
        time.sleep(0.4)
    return sock.exists()


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def diagnostics() -> dict:
    return {
        "xvfb": _have("Xvfb"),
        "x11vnc": _have("x11vnc"),
        "novnc_dir": novnc_dir() or "",
        "chromium": _chromium_available(),
        "vnc_port_open": _port_open(VNC_PORT),
    }


# --- VNC password -----------------------------------------------------------

def vnc_password() -> str:
    pw = db.get_setting("vnc_password")
    if not pw:
        pw = secrets.token_urlsafe(9)[:12]
        db.set_setting("vnc_password", pw)
    return pw


def export_netscape_cookies(cookies: list[dict], out_path: Path) -> int:
    """Write Playwright cookies as a Netscape cookies.txt (the format yt-dlp reads)."""
    lines = ["# Netscape HTTP Cookie File",
             "# Generated by ShortForge", ""]
    written = 0
    for c in cookies:
        domain = c.get("domain") or ""
        if not domain:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        expires = int(c.get("expires") or 0)
        if expires <= 0:
            expires = int(time.time()) + 180 * 24 * 3600  # session cookie -> keep it usable
        lines.append("\t".join([
            domain, include_sub, c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(expires), c.get("name") or "", c.get("value") or "",
        ]))
        written += 1
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def _write_vnc_passfile() -> Path:
    pw_file = config.DATA_DIR / ".vncpass"
    subprocess.run(["x11vnc", "-storepasswd", vnc_password(), str(pw_file)],
                   capture_output=True, check=False)
    return pw_file


# --- process helpers --------------------------------------------------------

def _kill(name: str) -> None:
    proc = _procs.pop(name, None)
    if not proc:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    # Reap so we don't leave zombies; never block the request for long.
    try:
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def _spawn(name: str, cmd: list[str], env: dict | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env={**os.environ, **(env or {})})
    _procs[name] = proc
    return proc


# --- the Playwright-driven session -----------------------------------------

class SessionRunner(threading.Thread):
    """Owns the headful browser for one account and reports what it sees."""

    def __init__(self, account_id: str, target: str = "tiktok",
                 start_url: str = "", prefill: Optional[dict] = None) -> None:
        super().__init__(daemon=True, name=f"session-{target}-{account_id}")
        self.account_id = account_id
        self.target = target if target in TARGETS else "tiktok"
        self.cfg = TARGETS[self.target]
        self.start_url = start_url or self.cfg["url"]
        self.callback_url = ""
        # {"video": path, "caption": text} -> attach the file and caption so the
        # user only has to press Post.
        self.prefill = prefill or {}
        self.logs: list[dict] = []
        self.logged_in = False
        self.username = ""
        self.cookie_count = 0
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.error = ""
        self._stop = threading.Event()
        self._commands: queue.Queue[str] = queue.Queue()

    # -- logging
    def log(self, message: str, level: str = "info") -> None:
        entry = {"t": time.time(), "level": level, "message": message}
        self.logs.append(entry)
        del self.logs[:-200]

    # -- lifecycle
    def request_stop(self, save: bool = True) -> None:
        self._commands.put("save" if save else "discard")
        self._stop.set()

    def run(self) -> None:  # noqa: C901 - linear browser lifecycle
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            self.error = f"Playwright unavailable: {exc}"
            self.log(self.error, "error")
            self.ready.set()
            self.finished.set()
            return

        profile = profile_path(self.account_id)
        self.log(f"Opening browser profile {profile.name}")
        lock = profile_lock(self.account_id)
        if not lock.acquire(timeout=60):
            self.error = ("This account's browser is already in use (a publish is "
                          "running). Try again in a moment.")
            self.log(self.error, "error")
            self.ready.set()
            self.finished.set()
            return
        clean_profile_locks(profile)
        try:
            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    str(profile),
                    headless=False,
                    env={**os.environ, **CHROMIUM_ENV, "DISPLAY": DISPLAY},
                    args=[
                        # --test-type hides Chromium's "unsupported command line
                        # flag --no-sandbox" infobar; --no-sandbox is required
                        # because the service usually runs as root.
                        "--test-type",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                        "--start-maximized",
                        "--window-position=0,0",
                        "--window-size=1440,900",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                    viewport=None,
                    locale="en-US",
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                label = self.cfg["label"]
                self.log(f"Browser ready — opening {label}")
                if self.target == "nordvpn":
                    self._watch_for_callback(page)
                try:
                    page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Could not open {label} ({exc}); you can navigate manually", "warn")
                # Signal readiness as soon as the page is up so the dashboard can
                # display the browser; attaching the file happens after, with the
                # user already watching, instead of behind a black screen.
                self.ready.set()
                if self.prefill.get("video"):
                    self._prefill_upload(page)
                    self.log("Ready — check the caption and press Post in the window", "success")
                else:
                    self.log(f"Waiting for you to log into {label}…")

                # Watch the session until the user presses Done / Stop.
                while not self._stop.is_set():
                    if self.target == "nordvpn":
                        if self._poll_nordvpn(page):
                            break
                        self._stop.wait(2.0)
                        continue
                    try:
                        cookies = ctx.cookies()
                    except Exception:  # noqa: BLE001
                        cookies = []
                    tt = [c for c in cookies
                          if self.cfg["domain"] in (c.get("domain") or "")]
                    wanted = self.cfg["session_cookies"]
                    session_ok = any(c.get("name") in wanted and c.get("value") for c in tt)
                    if session_ok and not self.logged_in:
                        self.logged_in = True
                        self.cookie_count = len(tt)
                        self.log(f"Login detected — {len(tt)} {label} cookies present", "success")
                        if self.target == "tiktok":
                            self._detect_username(page)
                    elif not session_ok and self.logged_in:
                        self.logged_in = False
                        self.log("Session cookie disappeared (logged out?)", "warn")
                    self._stop.wait(2.0)

                action = "save"
                try:
                    action = self._commands.get_nowait()
                except queue.Empty:
                    pass

                if action == "save" and self.target == "nordvpn":
                    if self.logged_in:
                        self.log("NordVPN is logged in — nothing else to store", "success")
                    else:
                        self.log("No NordVPN callback captured — login not completed", "warn")
                elif action == "save":
                    self.log("Saving session…")
                    try:
                        cookies = [c for c in ctx.cookies()
                                   if self.cfg["domain"] in (c.get("domain") or "")]
                        self.cookie_count = len(cookies)
                        if self.target == "youtube":
                            # yt-dlp reads data/cookies.txt (Netscape format).
                            out = config.DATA_DIR / "cookies.txt"
                            n = export_netscape_cookies(cookies, out)
                            self.log(f"Cookies captured and stored ({n}) → {out.name}", "success")
                            self.log("yt-dlp will now use them for downloads", "success")
                        else:
                            backup = config.DATA_DIR / "tiktok_cookies"
                            backup.mkdir(parents=True, exist_ok=True)
                            ctx.storage_state(path=str(backup / f"{self.account_id}.json"))
                            self.log(f"Cookies captured and stored ({len(cookies)})", "success")
                            # Confirm the auto-uploader can actually use them.
                            from . import tiktok_publish

                            usable = tiktok_publish.build_cookies_list(self.account_id)
                            if usable:
                                self.log(f"Auto-upload ready ({len(usable)} usable cookies)",
                                         "success")
                            else:
                                self.log("Cookies saved but unusable for auto-upload — "
                                         "make sure you are fully logged in", "warn")
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"Could not export cookies: {exc}", "warn")
                    if not self.logged_in:
                        self.log(f"No {label} session cookie found — you may not be logged in",
                                 "warn")

                self.log("Closing browser (profile is written to disk)…")
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                if action == "save" and self.target == "tiktok":
                    self.log("Profile saved — this account is ready to publish", "success")
                elif action == "save":
                    self.log("Done", "success")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.log(f"Session error: {exc}", "error")
        finally:
            clean_profile_locks(profile)
            try:
                lock.release()
            except RuntimeError:
                pass
            self.ready.set()
            self.finished.set()

    def _prefill_upload(self, page) -> None:
        """Attach the video and caption so the user only presses Post."""
        video = self.prefill.get("video", "")
        caption = self.prefill.get("caption", "")
        self.log("Attaching the video to TikTok's upload page…")
        deadline = time.time() + 90
        file_input = None
        while time.time() < deadline and file_input is None:
            for frame in [page] + list(page.frames):
                try:
                    el = frame.query_selector("input[type='file']")
                except Exception:  # noqa: BLE001
                    el = None
                if el:
                    file_input = el
                    break
            if file_input is None:
                page.wait_for_timeout(2000)
        if file_input is None:
            self.log("Upload page has no file input — log in first, then retry", "warn")
            return
        try:
            file_input.set_input_files(video)
            self.log("Video attached — TikTok is processing it", "success")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Could not attach the video: {exc}", "error")
            return
        if not caption:
            return
        page.wait_for_timeout(6000)
        for sel in ("div[contenteditable='true']", "div.public-DraftEditor-content"):
            try:
                box = page.query_selector(sel)
            except Exception:  # noqa: BLE001
                box = None
            if box:
                try:
                    box.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Delete")
                    box.type(caption[:2100], delay=6)
                    self.log("Caption filled", "success")
                except Exception:  # noqa: BLE001
                    self.log("Could not fill the caption — type it manually", "warn")
                break

    def _watch_for_callback(self, page) -> None:
        """Catch the nordvpn:// redirect the site fires right after sign-in."""
        def on_request(request) -> None:
            url = request.url or ""
            if url.startswith("nordvpn://") and not self.callback_url:
                self.callback_url = url
        try:
            page.on("request", on_request)
            page.on("framenavigated",
                    lambda frame: self._maybe_callback(getattr(frame, "url", "")))
        except Exception:  # noqa: BLE001
            pass

    def _maybe_callback(self, url: str) -> None:
        if url and url.startswith("nordvpn://") and not self.callback_url:
            self.callback_url = url

    def _poll_nordvpn(self, page) -> bool:
        """Look for the callback link; finish the CLI login when we find it.

        Returns True once the login is complete (or definitively failed).
        """
        if not self.callback_url:
            try:
                found = page.evaluate(
                    "() => {"
                    " const a = document.querySelector('a[href^=\"nordvpn://\"]');"
                    " if (a) return a.href;"
                    " const m = document.body ? document.body.innerHTML.match"
                    "(/nordvpn:\\/\\/[^\"'\\s<>]+/) : null;"
                    " return m ? m[0] : '';"
                    "}")
            except Exception:  # noqa: BLE001
                found = ""
            if found:
                self.callback_url = found
        if not self.callback_url:
            return False

        self.log("Login callback captured — finishing with the NordVPN CLI…", "success")
        from . import vpn

        try:
            vpn.complete_login(self.callback_url)
            self.logged_in = True
            self.log("NordVPN login complete", "success")
            st = vpn.status()
            if st.get("logged_in"):
                self.log("Account verified — connecting to a server…")
                try:
                    vpn.connect(None)
                    st = vpn.status()
                    self.log(f"Connected: {st.get('country') or 'server'} "
                             f"{st.get('ip') and 'IP ' + st['ip'] or ''}".strip(), "success")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Connected to account but could not connect: {exc}", "warn")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.log(f"CLI login failed: {exc}", "error")
        return True

    def _detect_username(self, page) -> None:
        """Best-effort: read the handle so the account gets a real name."""
        try:
            page.goto("https://www.tiktok.com/profile", wait_until="domcontentloaded",
                      timeout=30000)
            page.wait_for_timeout(2500)
            handle = page.evaluate(
                "() => { const m = document.body.innerText.match(/@[A-Za-z0-9._]{2,24}/);"
                " return m ? m[0] : ''; }")
            if handle:
                self.username = handle
                self.log(f"Signed in as {handle}", "success")
                db.update_tiktok_account(self.account_id, display_name=handle.lstrip("@"))
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> dict:
        return {
            "account_id": self.account_id,
            "target": self.target,
            "logged_in": self.logged_in,
            "username": self.username,
            "cookie_count": self.cookie_count,
            "ready": self.ready.is_set(),
            "finished": self.finished.is_set(),
            "error": self.error,
            "logs": self.logs[-80:],
        }


# --- public API -------------------------------------------------------------

def status() -> dict:
    with _lock:
        running = _runner is not None and not _runner.finished.is_set()
        snap = _runner.snapshot() if _runner else {}
    return {
        "running": running,
        "missing": missing_deps(),
        "vnc_password": vnc_password(),
        "vnc_port": VNC_PORT,
        "novnc_page": novnc_page(),
        "diagnostics": diagnostics(),
        "session": snap,
    }


def start_session(account_id: str, target: str = "tiktok", start_url: str = "",
                  prefill: Optional[dict] = None) -> dict:
    missing = missing_deps()
    blocking = [m for m in missing if m != "novnc"]
    if blocking:
        raise RuntimeError(
            f"Missing on the server: {', '.join(blocking)}. Run ./setup.sh again "
            f"(or: sudo apt install -y xvfb x11vnc).")

    stop_session(save=False)

    with _lock:
        # 1. Virtual display (kept across sessions)
        if not _procs.get("xvfb") or _procs["xvfb"].poll() is not None:
            _procs.pop("xvfb", None)
            _spawn("xvfb", ["Xvfb", DISPLAY, "-screen", "0", SCREEN, "-ac", "-nolisten", "tcp"])
        # Wait until X actually answers, otherwise x11vnc attaches to nothing
        # and the dashboard shows a black screen.
        if not _wait_for_display():
            raise RuntimeError(
                "The virtual display never came up. Check that Xvfb is installed "
                "(sudo apt install -y xvfb).")
        # A grey root makes it obvious the stream is live before Chromium paints.
        if _have("xsetroot"):
            subprocess.run(["xsetroot", "-solid", "grey20"],
                           env={**os.environ, "DISPLAY": DISPLAY},
                           capture_output=True, check=False)

        # 2. VNC server on that display, localhost only
        pw_file = _write_vnc_passfile()
        _spawn("x11vnc", [
            "x11vnc", "-display", DISPLAY, "-rfbport", str(VNC_PORT),
            "-rfbauth", str(pw_file), "-localhost", "-forever", "-shared",
            "-noxdamage", "-repeat",
        ])
        deadline = time.time() + 12
        while time.time() < deadline and not _port_open(VNC_PORT):
            time.sleep(0.3)
        if not _port_open(VNC_PORT):
            _kill("x11vnc")
            raise RuntimeError(
                "x11vnc did not start (port 5901 never opened). Check that Xvfb and "
                "x11vnc are installed and no other VNC server uses that port.")

        # 3. Playwright-driven headful Chromium with this account's profile
        global _runner
        _runner = SessionRunner(account_id, target, start_url, prefill)
        _runner.start()

    _runner.ready.wait(timeout=60)
    if _runner.error:
        raise RuntimeError(_runner.error)
    return status()


def stop_session(save: bool = True) -> dict:
    """Ask the browser to finish; returns once the profile is written."""
    global _runner
    with _lock:
        runner = _runner
    if runner and not runner.finished.is_set():
        runner.request_stop(save=save)
        runner.finished.wait(timeout=45)
    result = runner.snapshot() if runner else {}
    with _lock:
        _runner = None
        _kill("x11vnc")
    return result


def shutdown() -> None:
    try:
        stop_session(save=True)
    except Exception:  # noqa: BLE001
        pass
    _kill("xvfb")
