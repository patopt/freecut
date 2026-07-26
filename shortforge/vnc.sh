#!/usr/bin/env bash
# Start a standalone remote-browser session and print the VNC connection info.
#
# The dashboard normally drives this through noVNC in an iframe. This script
# does the same thing outside the app so you can attach with a real VNC client
# (RealVNC, TigerVNC, Remmina...). It reuses the app's stored VNC password and
# the same Chromium profiles, so a login done here is the one the uploader uses.
#
#   ./vnc.sh                 # localhost-only (safe) — connect over an SSH tunnel
#   ./vnc.sh --public        # listen on all interfaces (see the warning printed)
#   ./vnc.sh --profile tt1   # pick which Chromium profile to open
#   ./vnc.sh --stop          # tear the session down
set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
info() { echo -e "${BOLD}${GREEN}==>${RESET} $*"; }
warn() { echo -e "${BOLD}${YELLOW}!! ${RESET} $*"; }
die()  { echo -e "${BOLD}${RED}xx ${RESET} $*" >&2; exit 1; }

DISPLAY_NUM=":99"
VNC_PORT=5901
SCREEN="1440x900x24"
PROFILE="manual"
PUBLIC=0
STOP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --public)  PUBLIC=1 ;;
    --stop)    STOP=1 ;;
    --profile) PROFILE="${2:?--profile needs a name}"; shift ;;
    --port)    VNC_PORT="${2:?--port needs a number}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

stop_all() {
  pkill -f "x11vnc -display $DISPLAY_NUM" 2>/dev/null || true
  pkill -f "chromium.*--user-data-dir=$PWD/data/tiktok_profiles" 2>/dev/null || true
  pkill -f "Xvfb $DISPLAY_NUM" 2>/dev/null || true
  rm -f "/tmp/.X${DISPLAY_NUM#:}-lock"
}

if [ "$STOP" = "1" ]; then
  stop_all
  info "Session stopped."
  exit 0
fi

# --- checks -----------------------------------------------------------------
for b in Xvfb x11vnc; do
  command -v "$b" >/dev/null || die "$b is missing. Run: sudo apt install -y xvfb x11vnc"
done
[ -d venv ] || die "venv/ not found. Run ./setup.sh first."

if ss -ltn 2>/dev/null | grep -q ":$VNC_PORT "; then
  warn "Port $VNC_PORT is already in use — stopping the previous session first."
  stop_all
  sleep 1
fi

# --- password: the same one the dashboard shows -----------------------------
mkdir -p data
PASSWORD="$(venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '.')
from backend import db
from backend.pipeline import tiktok_session
db.init_db()          # a fresh install has no settings table yet
print(tiktok_session.vnc_password())
PY
)"
[ -n "$PASSWORD" ] || die "Could not read the VNC password from the database."

PASSFILE="$PWD/data/.vncpass"
x11vnc -storepasswd "$PASSWORD" "$PASSFILE" >/dev/null 2>&1

# --- start the stack --------------------------------------------------------
stop_all
sleep 0.5

info "Starting the virtual display..."
Xvfb "$DISPLAY_NUM" -screen 0 "$SCREEN" -ac -nolisten tcp >/dev/null 2>&1 &
for _ in $(seq 1 40); do
  DISPLAY="$DISPLAY_NUM" xdpyinfo >/dev/null 2>&1 && break
  sleep 0.25
done
DISPLAY="$DISPLAY_NUM" xdpyinfo >/dev/null 2>&1 \
  || die "The virtual display never came up (is xvfb installed?)."
command -v xsetroot >/dev/null && DISPLAY="$DISPLAY_NUM" xsetroot -solid grey20 || true

info "Starting the VNC server..."
BIND_ARGS=(-localhost)
if [ "$PUBLIC" = "1" ]; then BIND_ARGS=(); fi
x11vnc -display "$DISPLAY_NUM" -rfbport "$VNC_PORT" -rfbauth "$PASSFILE" \
       "${BIND_ARGS[@]}" -forever -shared -noxdamage -repeat >/dev/null 2>&1 &

for _ in $(seq 1 40); do
  ss -ltn 2>/dev/null | grep -q ":$VNC_PORT " && break
  sleep 0.3
done
ss -ltn 2>/dev/null | grep -q ":$VNC_PORT " \
  || die "x11vnc did not open port $VNC_PORT."

# --- Chromium on the app's own profile --------------------------------------
CHROME="$(venv/bin/python - <<'PY' 2>/dev/null || true
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
PY
)"
PROFILE_DIR="$PWD/data/tiktok_profiles/$PROFILE"
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/Singleton{Lock,Socket,Cookie}

if [ -n "$CHROME" ] && [ -x "$CHROME" ]; then
  info "Launching Chromium (profile: $PROFILE)..."
  DISPLAY="$DISPLAY_NUM" GOOGLE_API_KEY=no \
  GOOGLE_DEFAULT_CLIENT_ID=no GOOGLE_DEFAULT_CLIENT_SECRET=no \
  "$CHROME" --user-data-dir="$PROFILE_DIR" --no-first-run \
    --no-default-browser-check --start-maximized \
    --disable-blink-features=AutomationControlled \
    "https://www.tiktok.com/" >/dev/null 2>&1 &
else
  warn "Chromium not found — the display will be empty. Run ./setup.sh again."
fi

# --- connection info --------------------------------------------------------
PUBIP="$(curl -s --max-time 4 https://ifconfig.me 2>/dev/null || echo '<your-vps-ip>')"
SSHUSER="$(id -un)"
echo
echo "${BOLD}────────────  VNC connection info  ────────────${RESET}"
echo "  Password  : ${BOLD}$PASSWORD${RESET}"
echo "  Port      : $VNC_PORT   (display $DISPLAY_NUM, ${SCREEN%x*} px)"
echo "  Profile   : $PROFILE_DIR"
echo
if [ "$PUBLIC" = "1" ]; then
  echo "  ${BOLD}RealVNC address${RESET} : ${BOLD}$PUBIP:$VNC_PORT${RESET}"
  echo
  warn "Listening on ALL interfaces. Anyone who reaches this port and guesses"
  warn "the password controls a browser logged into your accounts. Restrict it:"
  echo "     sudo ufw allow from <your-home-ip> to any port $VNC_PORT proto tcp"
else
  echo "  Bound to localhost only. On ${BOLD}your machine${RESET}, open a tunnel:"
  echo
  echo "     ${BOLD}ssh -L $VNC_PORT:localhost:$VNC_PORT $SSHUSER@$PUBIP${RESET}"
  echo
  echo "  Leave that terminal open, then in RealVNC connect to:"
  echo "     ${BOLD}localhost:$VNC_PORT${RESET}"
fi
echo
echo "  Stop it with: ${BOLD}./vnc.sh --stop${RESET}"
echo "${BOLD}───────────────────────────────────────────────${RESET}"
