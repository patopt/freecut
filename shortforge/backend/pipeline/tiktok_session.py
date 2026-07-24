"""Remote browser session on the VPS, viewable from the dashboard.

Runs a real (headful) Chromium inside a virtual X display, exposes it over VNC,
and keeps a **persistent profile per TikTok account**. You log into TikTok once
through the dashboard, exactly as on a normal PC; the profile (cookies,
localStorage, device fingerprint) is reused for every later upload, so TikTok
sees one consistent browser.

Stack: Xvfb (virtual display) -> x11vnc (bound to localhost) -> the app's
WebSocket bridge -> noVNC in the dashboard. Nothing is exposed publicly except
through the password-protected dashboard.
"""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from .. import config, db

DISPLAY = ":99"
VNC_PORT = 5901
SCREEN = "1280x900x24"

_procs: dict[str, subprocess.Popen] = {}
_session: dict = {"account_id": None, "started_at": 0.0}


def profiles_dir() -> Path:
    d = config.DATA_DIR / "tiktok_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_path(account_id: str) -> Path:
    p = profiles_dir() / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def missing_deps() -> list[str]:
    missing = [c for c in ("Xvfb", "x11vnc") if not _have(c)]
    if novnc_dir() is None:
        missing.append("novnc")
    return missing


# Where the noVNC web client may live. The bundled copy under data/ is used
# when the distro package isn't available (setup.sh downloads it there).
NOVNC_CANDIDATES = (
    "/usr/share/novnc",
    "/usr/share/webapps/novnc",
    "/usr/local/share/novnc",
)


def novnc_dir() -> Optional[str]:
    """Return a directory that actually contains the noVNC client page."""
    candidates = list(NOVNC_CANDIDATES) + [str(config.DATA_DIR / "novnc")]
    for d in candidates:
        p = Path(d)
        if (p / "vnc.html").is_file() or (p / "vnc_lite.html").is_file():
            return str(p)
    return None


def novnc_page() -> str:
    """Filename of the client page available in the resolved noVNC dir."""
    d = novnc_dir()
    if d and (Path(d) / "vnc.html").is_file():
        return "vnc.html"
    return "vnc_lite.html"


def diagnostics() -> dict:
    """Everything the UI needs to explain a failed connection."""
    d = novnc_dir()
    return {
        "xvfb": _have("Xvfb"),
        "x11vnc": _have("x11vnc"),
        "novnc_dir": d or "",
        "novnc_page": novnc_page() if d else "",
        "chromium": _chromium_available(),
        "display_running": bool(_procs.get("xvfb")),
        "vnc_port_open": _port_open(VNC_PORT),
    }


def _chromium_available() -> bool:
    try:
        _chromium_binary()
        return True
    except Exception:  # noqa: BLE001
        return False


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def vnc_password() -> str:
    """Stable per-install VNC password (also usable from a desktop VNC app)."""
    pw = db.get_setting("vnc_password")
    if not pw:
        pw = secrets.token_urlsafe(9)[:12]
        db.set_setting("vnc_password", pw)
    return pw


def _write_vnc_passfile() -> Path:
    pw_file = config.DATA_DIR / ".vncpass"
    subprocess.run(["x11vnc", "-storepasswd", vnc_password(), str(pw_file)],
                   capture_output=True, check=False)
    return pw_file


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


def _spawn(name: str, cmd: list[str], env: dict | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env={**os.environ, **(env or {})})
    _procs[name] = proc
    return proc


def status() -> dict:
    return {
        "running": _session["account_id"] is not None,
        "account_id": _session["account_id"],
        "started_at": _session["started_at"],
        "missing": missing_deps(),
        "vnc_password": vnc_password(),
        "vnc_port": VNC_PORT,
        "novnc_page": novnc_page(),
        "diagnostics": diagnostics(),
    }


def start_session(account_id: str, url: str = "https://www.tiktok.com/login") -> dict:
    """Start (or restart) the remote browser for this account."""
    missing = missing_deps()
    if missing:
        raise RuntimeError(
            f"Missing on the server: {', '.join(missing)}. Run: "
            f"sudo apt install -y xvfb x11vnc")

    stop_session()

    # 1. Virtual display
    if not _procs.get("xvfb"):
        _spawn("xvfb", ["Xvfb", DISPLAY, "-screen", "0", SCREEN, "-ac", "-nolisten", "tcp"])
        time.sleep(1.5)

    # 2. VNC server on that display, localhost only (the dashboard bridges it)
    pw_file = _write_vnc_passfile()
    # NOTE: never pass -nopw together with -rfbauth; they contradict each other
    # and x11vnc then refuses the password noVNC sends.
    _spawn("x11vnc", [
        "x11vnc", "-display", DISPLAY, "-rfbport", str(VNC_PORT),
        "-rfbauth", str(pw_file), "-localhost", "-forever", "-shared",
        "-noxdamage", "-repeat",
    ])
    deadline = time.time() + 12
    while time.time() < deadline and not _port_open(VNC_PORT):
        time.sleep(0.3)
    if not _port_open(VNC_PORT):
        stop_session()
        raise RuntimeError(
            "x11vnc did not start (port 5901 never opened). Check that Xvfb and "
            "x11vnc are installed and that no other VNC server uses that port.")

    # 3. Headful Chromium with the account's persistent profile
    _spawn("chromium", _chromium_cmd(account_id, url), env={"DISPLAY": DISPLAY})

    _session["account_id"] = account_id
    _session["started_at"] = time.time()
    return status()


def _chromium_cmd(account_id: str, url: str) -> list[str]:
    binary = _chromium_binary()
    return [
        binary,
        f"--user-data-dir={profile_path(account_id)}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-position=0,0", "--window-size=1280,900",
        "--start-maximized", "--no-sandbox", "--disable-dev-shm-usage",
        url,
    ]


def _chromium_binary() -> str:
    """Prefer Playwright's Chromium (installed by setup.sh), else a system one."""
    for env_key in ("PLAYWRIGHT_BROWSERS_PATH",):
        base = os.environ.get(env_key)
        if base:
            for c in Path(base).glob("chromium*/chrome-linux/chrome"):
                return str(c)
    home = Path.home() / ".cache/ms-playwright"
    for c in sorted(home.glob("chromium*/chrome-linux/chrome")):
        return str(c)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if _have(name):
            return name
    raise RuntimeError("No Chromium found. Run: python -m playwright install chromium")


def stop_session() -> None:
    for name in ("chromium", "x11vnc"):
        _kill(name)
    _session["account_id"] = None
    _session["started_at"] = 0.0


def shutdown() -> None:
    stop_session()
    _kill("xvfb")


def has_profile(account_id: str) -> bool:
    p = profiles_dir() / account_id
    return p.exists() and any(p.iterdir())


def profile_dir_for(account_id: str) -> Optional[str]:
    p = profiles_dir() / account_id
    return str(p) if p.exists() else None
