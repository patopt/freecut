"""NordVPN CLI wrapper: login, connect, and rotate servers when we get blocked.

YouTube (and TikTok) rate-limit or bot-check datacenter IPs. Rotating to a
different NordVPN server gives the downloader a fresh residential-looking exit
IP, which is the most reliable way to get past those blocks.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import threading
import time
from typing import Optional

from .. import db

# A spread of countries with good NordVPN coverage; rotation picks from these.
COUNTRIES = [
    "France", "Belgium", "Netherlands", "Germany", "Spain", "Italy",
    "United_Kingdom", "Switzerland", "Sweden", "Poland", "Portugal",
    "Ireland", "Austria", "Denmark", "Canada", "United_States",
]

_lock = threading.RLock()
_last_rotation = 0.0
MIN_ROTATION_GAP = 20.0  # seconds; avoid hammering the CLI


def available() -> bool:
    return shutil.which("nordvpn") is not None


def _run(args: list[str], timeout: int = 90) -> tuple[int, str]:
    try:
        proc = subprocess.run(["nordvpn", *args], capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "nordvpn CLI is not installed"
    except subprocess.TimeoutExpired:
        return 124, "nordvpn command timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def status() -> dict:
    if not available():
        return {"installed": False, "logged_in": False, "connected": False,
                "server": "", "country": "", "ip": "", "raw": ""}
    _code, out = _run(["status"], timeout=20)
    text = out.strip()
    connected = "Connected" in text and "Disconnected" not in text.split("Status:")[-1][:40]
    info = {"installed": True, "connected": connected, "raw": text}
    for line in text.splitlines():
        low = line.lower()
        if "hostname" in low or "server:" in low:
            info["server"] = line.split(":", 1)[-1].strip()
        elif low.startswith("country"):
            info["country"] = line.split(":", 1)[-1].strip()
        elif "ip:" in low:
            info["ip"] = line.split(":", 1)[-1].strip()
    _code, acct = _run(["account"], timeout=20)
    info["logged_in"] = "not logged in" not in acct.lower() and "Email" in acct
    info.setdefault("server", "")
    info.setdefault("country", "")
    info.setdefault("ip", "")
    return info


def login_url() -> str:
    """Ask the CLI for a browser login link (headless-friendly)."""
    code, out = _run(["login"], timeout=60)
    for token in out.split():
        if token.startswith("https://"):
            return token.strip().rstrip(".,")
    if code != 0:
        raise RuntimeError(out.strip() or "nordvpn login failed")
    return ""


def login_with_token(token: str) -> str:
    code, out = _run(["login", "--token", token.strip()], timeout=90)
    if code != 0:
        raise RuntimeError(out.strip() or "Token login failed")
    return out.strip()


def logout() -> str:
    _code, out = _run(["logout", "--persist-token"], timeout=60)
    return out.strip()


def connect(country: Optional[str] = None) -> str:
    args = ["connect"]
    if country:
        args.append(country)
    code, out = _run(args, timeout=120)
    if code != 0:
        raise RuntimeError(out.strip() or "nordvpn connect failed")
    return out.strip()


def disconnect() -> str:
    _code, out = _run(["disconnect"], timeout=60)
    return out.strip()


def rotate(reason: str = "") -> bool:
    """Switch to a different random server. Returns True if it connected."""
    global _last_rotation
    if not available() or not db.is_vpn_rotation_enabled():
        return False
    with _lock:
        if time.time() - _last_rotation < MIN_ROTATION_GAP:
            time.sleep(MIN_ROTATION_GAP - (time.time() - _last_rotation))
        current = status()
        choices = [c for c in COUNTRIES if c.replace("_", " ") != current.get("country", "")]
        target = random.choice(choices or COUNTRIES)
        try:
            connect(target)
            _last_rotation = time.time()
            new = status()
            db.log_activity(
                "vpn", f"VPN rotated to {new.get('country') or target}",
                (reason or "blocked")[:200], "info", "vpn", "")
            return True
        except Exception as exc:  # noqa: BLE001
            _last_rotation = time.time()
            db.log_activity("vpn", "VPN rotation failed", str(exc)[:200], "error", "vpn", "")
            return False


def ensure_connected() -> bool:
    """Connect if the CLI is installed, logged in and not yet connected."""
    if not available() or not db.is_vpn_rotation_enabled():
        return False
    st = status()
    if not st.get("logged_in"):
        return False
    if st.get("connected"):
        return True
    try:
        connect(random.choice(COUNTRIES))
        return True
    except Exception:  # noqa: BLE001
        return False
