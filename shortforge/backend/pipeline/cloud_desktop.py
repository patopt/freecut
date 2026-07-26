"""A full remote desktop, streamed into the dashboard over noVNC.

The TikTok remote browser (`tiktok_session`) shows a single Playwright-driven
Chromium on a bare X display. This module is the other half: a real desktop
session — window manager, panel, file manager, terminal, browser — so the VPS
can be used like a normal machine from the "Cloud" tab.

It deliberately runs on its **own display and VNC port** so opening the cloud
desktop never disturbs an in-progress TikTok/YouTube login, and vice versa.
Everything is on demand: nothing is spawned until the user presses Start, and
Stop reclaims the memory.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .. import config
from . import tiktok_session

DISPLAY = ":98"
VNC_PORT = 5902
SCREEN = "1440x900x24"

_procs: dict[str, subprocess.Popen] = {}
_lock = threading.RLock()

# Desktop stacks in order of preference. The first one whose entry binary is
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


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


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


def _spawn(name: str, cmd: list[str], env: Optional[dict] = None) -> None:
    _procs[name] = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env={**os.environ, **(env or {})},
    )


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
    try:
        proc.wait(timeout=4)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def _wait_for_display(timeout: float = 15.0) -> bool:
    env = {**os.environ, "DISPLAY": DISPLAY}
    sock = Path(f"/tmp/.X11-unix/X{DISPLAY.lstrip(':')}")
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


def status() -> dict:
    running = _port_open(VNC_PORT)
    return {
        "running": running,
        "desktop": desktop_name(),
        "missing": missing_deps(),
        "vnc_password": tiktok_session.vnc_password(),
        "vnc_port": VNC_PORT,
        "novnc_page": tiktok_session.novnc_page(),
        "screen": SCREEN.rsplit("x", 1)[0],
    }


def start() -> dict:
    missing = missing_deps()
    if missing:
        raise RuntimeError(
            f"Missing on the server: {', '.join(missing)}. Run ./setup.sh again "
            f"(or: sudo apt install -y xvfb x11vnc xfce4 xfce4-terminal).")

    with _lock:
        if _port_open(VNC_PORT):
            return status()

        picked = _pick_desktop()
        assert picked is not None  # missing_deps() already guaranteed this
        label, session_cmd, extras = picked

        _spawn("xvfb", ["Xvfb", DISPLAY, "-screen", "0", SCREEN, "-ac", "-nolisten", "tcp"])
        if not _wait_for_display():
            _kill("xvfb")
            raise RuntimeError("The virtual display never came up (is xvfb installed?).")

        env = {"DISPLAY": DISPLAY, "HOME": os.path.expanduser("~"),
               "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
               # Chromium's own Safe-Browsing keys; unrelated to Gemini. Set
               # here so the browser launched from the desktop menu is quiet too.
               "GOOGLE_API_KEY": "no", "GOOGLE_DEFAULT_CLIENT_ID": "no",
               "GOOGLE_DEFAULT_CLIENT_SECRET": "no"}

        if _have("xsetroot"):
            subprocess.run(["xsetroot", "-solid", "#1b2430"], env={**os.environ, **env},
                           capture_output=True, check=False)

        pw_file = config.DATA_DIR / ".vncpass-cloud"
        subprocess.run(["x11vnc", "-storepasswd", tiktok_session.vnc_password(), str(pw_file)],
                       capture_output=True, check=False)
        _spawn("x11vnc", [
            "x11vnc", "-display", DISPLAY, "-rfbport", str(VNC_PORT),
            "-rfbauth", str(pw_file), "-localhost", "-forever", "-shared",
            "-noxdamage", "-repeat",
        ])
        deadline = time.time() + 12
        while time.time() < deadline and not _port_open(VNC_PORT):
            time.sleep(0.3)
        if not _port_open(VNC_PORT):
            stop()
            raise RuntimeError(f"x11vnc did not open port {VNC_PORT}.")

        _spawn("session", session_cmd, env)
        for i, extra in enumerate(extras):
            time.sleep(0.6)  # let the WM map its root window before panels attach
            _spawn(f"extra{i}", extra, env)

    return {**status(), "desktop": label}


def launch_browser(profile: str = "cloud", url: str = "https://www.google.com") -> None:
    """Open Chromium on the desktop, on one of the app's own profiles."""
    if not _port_open(VNC_PORT):
        raise RuntimeError("The cloud desktop is not running.")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            chrome = p.chromium.executable_path
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Chromium is unavailable: {exc}") from exc

    profile_dir = tiktok_session.profile_path(profile)
    tiktok_session.clean_profile_locks(profile_dir)
    _spawn("chromium", [
        chrome, f"--user-data-dir={profile_dir}", "--no-first-run",
        "--no-default-browser-check", "--start-maximized",
        "--disable-blink-features=AutomationControlled", url,
    ], {"DISPLAY": DISPLAY, "GOOGLE_API_KEY": "no",
        "GOOGLE_DEFAULT_CLIENT_ID": "no", "GOOGLE_DEFAULT_CLIENT_SECRET": "no"})


def stop() -> dict:
    with _lock:
        # Xvfb goes first on purpose: every desktop app loses its X connection
        # and exits on its own, which is the only reliable way to reap the
        # grandchildren a session manager forks.
        for name in ["xvfb", *[n for n in _procs if n != "xvfb"]]:
            _kill(name)
        # Belt and braces for a session left behind by a previous process (a
        # restarted app has an empty _procs). Both of these carry the display
        # in their argv, so the match is exact.
        subprocess.run(["pkill", "-f", f"x11vnc -display {DISPLAY}"], capture_output=True, check=False)
        subprocess.run(["pkill", "-f", f"Xvfb {DISPLAY}"], capture_output=True, check=False)
        Path(f"/tmp/.X{DISPLAY.lstrip(':')}-lock").unlink(missing_ok=True)
    return status()
