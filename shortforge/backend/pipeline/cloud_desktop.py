"""Full remote desktops, streamed into the dashboard over noVNC.

The TikTok remote browser (`tiktok_session`) shows a single Playwright-driven
Chromium on a bare X display. This module is the other half: real desktop
sessions — window manager, panel, file manager, terminal, browser — so the VPS
can be used like a normal machine from the "Cloud" tab.

Several sessions can run side by side, each on its **own display and VNC
port**, so they never disturb each other nor an in-progress TikTok/YouTube
login. Each is created at the resolution the browser reports and can be
resized afterwards without losing the session.

Everything is on demand: nothing is spawned until the user presses Start, and
stopping a session reclaims its memory.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config
from . import tiktok_session

# Each session takes a display and the matching VNC port. Kept well away from
# tiktok_session's :99 / 5901.
BASE_DISPLAY = 90
BASE_PORT = 5902
# A desktop plus a browser is roughly 600 MB. Three is already ambitious on a
# 4 GB box, and the cap is what stops the OOM killer from arbitrating instead.
MAX_SESSIONS = 3

DEFAULT_W, DEFAULT_H = 1440, 900
MIN_W, MIN_H = 800, 600
MAX_W, MAX_H = 2560, 1600

_sessions: dict[str, "Session"] = {}
_lock = threading.RLock()

# Desktop stacks in order of preference. The first one whose session binary is
# installed wins; each entry is (label, session command, extra background apps).
DESKTOPS = (
    ("XFCE", ["dbus-launch", "--exit-with-session", "xfce4-session"], []),
    ("XFCE", ["dbus-launch", "--exit-with-session", "startxfce4"], []),
    ("LXDE", ["dbus-launch", "--exit-with-session", "startlxde"], []),
    # Fluxbox has no session manager, so the panel and desktop icons are
    # separate processes we start ourselves.
    ("Fluxbox", ["fluxbox"], [["tint2"], ["pcmanfm", "--desktop"]]),
    ("Openbox", ["openbox-session"], [["tint2"], ["pcmanfm", "--desktop"]]),
)


@dataclass
class Session:
    id: str
    slot: int
    width: int
    height: int
    desktop: str
    created_at: float
    procs: dict[str, subprocess.Popen] = field(default_factory=dict)

    @property
    def display(self) -> str:
        return f":{BASE_DISPLAY + self.slot}"

    @property
    def port(self) -> int:
        return BASE_PORT + self.slot

    def public(self) -> dict:
        return {
            "id": self.id, "slot": self.slot, "port": self.port,
            "display": self.display, "width": self.width, "height": self.height,
            "desktop": self.desktop, "created_at": self.created_at,
            "running": _port_open(self.port),
        }


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _clamp(v, lo: int, hi: int, fallback: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return fallback
    # Odd widths break some X clients and every video encoder downstream.
    return max(lo, min(hi, n - (n % 2)))


def _pick_desktop() -> Optional[tuple[str, list[str], list[list[str]]]]:
    for label, cmd, extras in DESKTOPS:
        # dbus-launch is a wrapper — the session binary is what must exist.
        entry = cmd[-1]
        if _have(entry) and (cmd[0] == entry or _have(cmd[0])):
            return label, cmd, [e for e in extras if _have(e[0])]
    return None


def desktop_name() -> str:
    picked = _pick_desktop()
    return picked[0] if picked else ""


def missing_deps() -> list[str]:
    missing = [c for c in ("Xvfb", "x11vnc") if not _have(c)]
    if not _pick_desktop():
        missing.append("a desktop environment")
    return missing


# --- process helpers --------------------------------------------------------

def _spawn(sess: Session, name: str, cmd: list[str], env: Optional[dict] = None) -> None:
    sess.procs[name] = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env={**os.environ, **(env or {})},
    )


def _kill(sess: Session, name: str) -> None:
    proc = sess.procs.pop(name, None)
    if not proc:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=4)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def _wait_for_display(display: str, timeout: float = 15.0) -> bool:
    env = {**os.environ, "DISPLAY": display}
    sock = Path(f"/tmp/.X11-unix/X{display.lstrip(':')}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sock.exists():
            if not _have("xdpyinfo"):
                return True
            probe = subprocess.run(["xdpyinfo"], env=env, capture_output=True, check=False)
            if probe.returncode == 0:
                return True
        time.sleep(0.25)
    return False


def _session_env(sess: Session) -> dict:
    return {
        "DISPLAY": sess.display,
        "HOME": os.path.expanduser("~"),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        # Chromium's own Safe-Browsing keys; unrelated to Gemini. Set here so a
        # browser launched from the desktop menu is quiet too.
        "GOOGLE_API_KEY": "no",
        "GOOGLE_DEFAULT_CLIENT_ID": "no",
        "GOOGLE_DEFAULT_CLIENT_SECRET": "no",
    }


# --- public API -------------------------------------------------------------

def status() -> dict:
    with _lock:
        # Drop sessions whose X server died under us so the slot frees up.
        for sid in [s.id for s in _sessions.values() if not _port_open(s.port)]:
            _sessions.pop(sid, None)
        sessions = [s.public() for s in
                    sorted(_sessions.values(), key=lambda s: s.created_at)]
    return {
        "sessions": sessions,
        "max_sessions": MAX_SESSIONS,
        "desktop": desktop_name(),
        "missing": missing_deps(),
        "vnc_password": tiktok_session.vnc_password(),
        "novnc_page": tiktok_session.novnc_page(),
    }


def get(session_id: str) -> Optional[Session]:
    with _lock:
        return _sessions.get(session_id)


def start(width: int = DEFAULT_W, height: int = DEFAULT_H) -> dict:
    missing = missing_deps()
    if missing:
        raise RuntimeError(
            f"Missing on the server: {', '.join(missing)}. Run ./setup.sh again "
            f"(or: sudo apt install -y xvfb x11vnc xfce4-session xfce4-panel).")

    width = _clamp(width, MIN_W, MAX_W, DEFAULT_W)
    height = _clamp(height, MIN_H, MAX_H, DEFAULT_H)

    with _lock:
        status()  # prune dead sessions first so a freed slot is reusable
        if len(_sessions) >= MAX_SESSIONS:
            raise RuntimeError(
                f"{MAX_SESSIONS} desktops are already running — stop one first.")
        used = {s.slot for s in _sessions.values()}
        slot = next((i for i in range(MAX_SESSIONS) if i not in used), None)
        if slot is None:
            raise RuntimeError("No free desktop slot.")

        picked = _pick_desktop()
        assert picked is not None  # missing_deps() already guaranteed this
        label, session_cmd, extras = picked

        sess = Session(id=uuid.uuid4().hex[:8], slot=slot, width=width,
                       height=height, desktop=label, created_at=time.time())
        # A stale lock from a killed X server blocks the display forever.
        Path(f"/tmp/.X{BASE_DISPLAY + slot}-lock").unlink(missing_ok=True)

        _spawn(sess, "xvfb", ["Xvfb", sess.display, "-screen", "0",
                              f"{width}x{height}x24", "-ac", "-nolisten", "tcp"])
        if not _wait_for_display(sess.display):
            _kill(sess, "xvfb")
            raise RuntimeError("The virtual display never came up (is xvfb installed?).")

        env = _session_env(sess)
        if _have("xsetroot"):
            subprocess.run(["xsetroot", "-solid", "#1b2430"],
                           env={**os.environ, **env}, capture_output=True, check=False)

        pw_file = config.DATA_DIR / f".vncpass-cloud{slot}"
        subprocess.run(["x11vnc", "-storepasswd", tiktok_session.vnc_password(), str(pw_file)],
                       capture_output=True, check=False)
        # -xrandr makes x11vnc follow server-side resolution changes and push
        # the new framebuffer size to connected clients, which is what lets
        # resize() work without dropping the session.
        _spawn(sess, "x11vnc", [
            "x11vnc", "-display", sess.display, "-rfbport", str(sess.port),
            "-rfbauth", str(pw_file), "-localhost", "-forever", "-shared",
            "-noxdamage", "-repeat", "-xrandr", "resize",
        ])
        deadline = time.time() + 12
        while time.time() < deadline and not _port_open(sess.port):
            time.sleep(0.3)
        if not _port_open(sess.port):
            for name in list(sess.procs):
                _kill(sess, name)
            raise RuntimeError(f"x11vnc did not open port {sess.port}.")

        _spawn(sess, "session", session_cmd, env)
        for i, extra in enumerate(extras):
            time.sleep(0.6)  # let the WM map its root window before panels attach
            _spawn(sess, f"extra{i}", extra, env)

        _sessions[sess.id] = sess
        return sess.public()


def resize(session_id: str, width: int, height: int) -> dict:
    """Change a live session's resolution through xrandr, keeping it running."""
    sess = get(session_id)
    if sess is None:
        raise RuntimeError("That desktop is not running.")
    width = _clamp(width, MIN_W, MAX_W, sess.width)
    height = _clamp(height, MIN_H, MAX_H, sess.height)
    if (width, height) == (sess.width, sess.height):
        return sess.public()
    if not _have("xrandr"):
        raise RuntimeError("xrandr is not installed (sudo apt install -y x11-xserver-utils).")

    env = {**os.environ, "DISPLAY": sess.display}
    mode = f"sf_{width}x{height}"

    # Xvfb only advertises the mode it was started with, so the new one has to
    # be defined before it can be selected. cvt computes a valid modeline.
    modeline = ""
    if _have("cvt"):
        out = subprocess.run(["cvt", str(width), str(height)],
                             capture_output=True, text=True, check=False).stdout
        m = re.search(r'Modeline\s+"[^"]*"\s+(.*)', out)
        if m:
            modeline = m.group(1).strip()
    if not modeline:
        # Rough CVT-less fallback: a 60 Hz timing good enough for a virtual head.
        modeline = (f"{width * height * 60 / 1_000_000:.2f} {width} {width + 48} "
                    f"{width + 112} {width + 160} {height} {height + 3} "
                    f"{height + 9} {height + 12} -hsync +vsync")

    query = subprocess.run(["xrandr", "--query"], env=env,
                           capture_output=True, text=True, check=False).stdout
    m = re.search(r"^(\S+)\s+connected", query, re.M) or re.search(r"^(\S+)\s+", query, re.M)
    output = m.group(1) if m else "screen"

    subprocess.run(["xrandr", "--newmode", mode, *modeline.split()],
                   env=env, capture_output=True, check=False)
    subprocess.run(["xrandr", "--addmode", output, mode],
                   env=env, capture_output=True, check=False)
    applied = subprocess.run(["xrandr", "--output", output, "--mode", mode],
                             env=env, capture_output=True, text=True, check=False)
    if applied.returncode != 0:
        raise RuntimeError(
            f"Could not resize this desktop: {applied.stderr.strip() or 'xrandr refused the mode'}")

    with _lock:
        sess.width, sess.height = width, height
    return sess.public()


def launch_browser(session_id: str, profile: str = "cloud",
                   url: str = "https://www.google.com") -> None:
    """Open Chromium on a desktop, on one of the app's own profiles."""
    sess = get(session_id)
    if sess is None or not _port_open(sess.port):
        raise RuntimeError("That desktop is not running.")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            chrome = p.chromium.executable_path
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Chromium is unavailable: {exc}") from exc

    profile_dir = tiktok_session.profile_path(profile)
    tiktok_session.clean_profile_locks(profile_dir)
    _spawn(sess, f"chromium{time.time():.0f}", [
        chrome, f"--user-data-dir={profile_dir}", "--no-first-run",
        "--no-default-browser-check", "--start-maximized",
        "--disable-blink-features=AutomationControlled", url,
    ], _session_env(sess))


def stop(session_id: str) -> dict:
    with _lock:
        sess = _sessions.pop(session_id, None)
    if sess is not None:
        _teardown(sess)
    return status()


def stop_all() -> dict:
    with _lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for sess in sessions:
        _teardown(sess)
    return status()


def _teardown(sess: Session) -> None:
    # Xvfb goes first on purpose: every desktop app loses its X connection and
    # exits on its own, which is the only reliable way to reap the
    # grandchildren a session manager forks.
    for name in ["xvfb", *[n for n in sess.procs if n != "xvfb"]]:
        _kill(sess, name)
    # Belt and braces for a session left behind by a previous process (a
    # restarted app has an empty registry). Both carry the display in their
    # argv, so the match is exact.
    subprocess.run(["pkill", "-f", f"x11vnc -display {sess.display}"],
                   capture_output=True, check=False)
    subprocess.run(["pkill", "-f", f"Xvfb {sess.display}"],
                   capture_output=True, check=False)
    Path(f"/tmp/.X{sess.display.lstrip(':')}-lock").unlink(missing_ok=True)
