#!/usr/bin/env bash
# Start a standalone remote-browser session, reachable over VNC and RDP.
#
# The dashboard normally drives this through noVNC in an iframe. This script
# does the same thing outside the app so you can attach with a real VNC client
# (RealVNC, TigerVNC, Remmina...) or with any RDP client (Windows "Remote
# Desktop Connection", Microsoft Remote Desktop on macOS/iOS/Android, FreeRDP).
# It reuses the app's stored password and the same Chromium profiles, so a
# login done here is the one the uploader uses.
#
# RDP is set up automatically: xrdp is installed if missing and configured to
# proxy the running VNC display, so both protocols show the same browser.
# Unlike raw VNC, RDP is TLS-encrypted — prefer it over --public.
#
#   ./vnc.sh                 # RDP on 3389 + VNC on localhost (SSH tunnel)
#   ./vnc.sh --public        # also expose VNC itself on all interfaces
#   ./vnc.sh --no-rdp        # skip the RDP setup entirely
#   ./vnc.sh --profile tt1   # pick which Chromium profile to open
#   ./vnc.sh --stop          # tear the session down
set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
info() { echo -e "${BOLD}${GREEN}==>${RESET} $*"; }
warn() { echo -e "${BOLD}${YELLOW}!! ${RESET} $*"; }
die()  { echo -e "${BOLD}${RED}xx ${RESET} $*" >&2; exit 1; }

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

DISPLAY_NUM=":99"
VNC_PORT=5901
RDP_PORT=3389
SCREEN="1440x900x24"
PROFILE="manual"
PUBLIC=0
STOP=0
WANT_RDP=1

while [ $# -gt 0 ]; do
  case "$1" in
    --public)   PUBLIC=1 ;;
    --stop)     STOP=1 ;;
    --no-rdp)   WANT_RDP=0 ;;
    --rdp)      WANT_RDP=1 ;;
    --profile)  PROFILE="${2:?--profile needs a name}"; shift ;;
    --port)     VNC_PORT="${2:?--port needs a number}"; shift ;;
    --rdp-port) RDP_PORT="${2:?--rdp-port needs a number}"; shift ;;
    -h|--help)  sed -n '2,19p' "$0"; exit 0 ;;
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
  # Don't leave an RDP door open onto a display that no longer exists.
  if systemctl list-unit-files 2>/dev/null | grep -q '^xrdp\.service'; then
    $SUDO systemctl stop xrdp xrdp-sesman 2>/dev/null || true
    info "xrdp stopped."
  fi
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

# --- RDP (xrdp proxying the VNC display) ------------------------------------
# xrdp's libvnc backend forwards an RDP client straight to a VNC server, so
# there is no second X session and no desktop environment to install — the RDP
# and VNC clients see the exact same Chromium, for ~15 MB of extra RAM.
RDP_READY=0
setup_rdp() {
  if ! command -v xrdp >/dev/null 2>&1; then
    if ! command -v apt-get >/dev/null 2>&1; then
      warn "xrdp is not installed and apt-get is unavailable — skipping RDP."
      return 1
    fi
    info "Installing xrdp..."
    $SUDO apt-get update -qq >/dev/null 2>&1 || true
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xrdp >/dev/null 2>&1 \
      || { warn "Could not install xrdp — skipping RDP."; return 1; }
  fi

  local lib
  lib="$(ls /usr/lib/xrdp/libvnc.so /usr/lib/*/xrdp/libvnc.so 2>/dev/null | head -1)"
  [ -n "$lib" ] || { warn "xrdp is installed without libvnc.so — skipping RDP."; return 1; }

  # Keep the distro file around; ours is regenerated on every run because the
  # VNC port can change between runs.
  [ -f /etc/xrdp/xrdp.ini ] && [ ! -f /etc/xrdp/xrdp.ini.shortforge-bak ] \
    && $SUDO cp /etc/xrdp/xrdp.ini /etc/xrdp/xrdp.ini.shortforge-bak

  $SUDO tee /etc/xrdp/xrdp.ini >/dev/null <<EOF
; Generated by ShortForge vnc.sh — the original is at xrdp.ini.shortforge-bak
[Globals]
ini_version=1
fork=true
port=$RDP_PORT
use_vsock=false
security_layer=negotiate
crypt_level=high
ssl_protocols=TLSv1.2, TLSv1.3
autorun=shortforge
allow_channels=true
max_bpp=24
new_cursors=true
bitmap_compression=true
bulk_compression=true
tcp_nodelay=true
tcp_keepalive=true

[Logging]
LogFile=/var/log/xrdp.log
LogLevel=INFO
EnableSyslog=true

[shortforge]
name=ShortForge Remote Browser
lib=$lib
ip=127.0.0.1
port=$VNC_PORT
username=na
password=ask
delay_ms=1000
EOF

  # sesman spawns per-user X sessions we do not use; leaving it off saves RAM
  # and removes a second way in.
  $SUDO systemctl disable --now xrdp-sesman >/dev/null 2>&1 || true
  $SUDO systemctl enable xrdp >/dev/null 2>&1 || true
  $SUDO systemctl restart xrdp >/dev/null 2>&1 \
    || { warn "xrdp failed to start (see: journalctl -u xrdp -n 40)."; return 1; }

  local n
  for n in $(seq 1 20); do
    ss -ltn 2>/dev/null | grep -q ":$RDP_PORT " && break
    sleep 0.3
  done
  ss -ltn 2>/dev/null | grep -q ":$RDP_PORT " \
    || { warn "xrdp did not open port $RDP_PORT."; return 1; }

  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -qi '^Status: active'; then
    ufw status 2>/dev/null | grep -q "$RDP_PORT" || $SUDO ufw allow "$RDP_PORT"/tcp >/dev/null 2>&1 || true
  fi
  return 0
}

if [ "$WANT_RDP" = "1" ]; then
  info "Configuring RDP..."
  setup_rdp && RDP_READY=1
fi

# --- connection info --------------------------------------------------------
PUBIP="$(curl -s --max-time 4 https://ifconfig.me 2>/dev/null || echo '<your-vps-ip>')"
SSHUSER="$(id -un)"
echo
echo "${BOLD}──────────────  Connection info  ──────────────${RESET}"
echo "  Password  : ${BOLD}${PASSWORD:0:8}${RESET}"
echo "              (VNC auth ignores anything past 8 characters)"
echo "  Display   : $DISPLAY_NUM  ${SCREEN%x*} px    Profile: $PROFILE"
echo

if [ "$RDP_READY" = "1" ]; then
  echo "${BOLD}  RDP  (recommended — encrypted)${RESET}"
  echo "     Address  : ${BOLD}$PUBIP:$RDP_PORT${RESET}"
  echo "     Username : ${BOLD}na${RESET}   (anything works)"
  echo "     Password : ${BOLD}${PASSWORD:0:8}${RESET}"
  echo "     Windows: 'Remote Desktop Connection'. macOS/iOS/Android:"
  echo "     'Microsoft Remote Desktop'. Linux: Remmina."
  echo "     The certificate is self-signed — accept the warning once."
  echo
elif [ "$WANT_RDP" = "1" ]; then
  warn "RDP is not available this run — see the messages above."
  echo
fi

echo "${BOLD}  VNC${RESET}"
if [ "$PUBLIC" = "1" ]; then
  echo "  ${BOLD}RealVNC address${RESET} : ${BOLD}$PUBIP:$VNC_PORT${RESET}"
  echo
  warn "Listening on ALL interfaces, and RFB traffic is not encrypted."
  warn "Anyone reaching this port with the 8-char password drives a browser"
  warn "logged into your accounts. Restrict it to your own IP:"
  echo "     sudo ufw allow from <your-home-ip> to any port $VNC_PORT proto tcp"
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -qi '^Status: active'; then
    ufw status 2>/dev/null | grep -q "$VNC_PORT" \
      || warn "ufw is active and does not allow $VNC_PORT yet — you will not connect."
  fi
else
  echo "     Bound to localhost. On ${BOLD}your machine${RESET}, open a tunnel:"
  echo "       ${BOLD}ssh -L $VNC_PORT:localhost:$VNC_PORT $SSHUSER@$PUBIP${RESET}"
  echo "     then point RealVNC at ${BOLD}localhost:$VNC_PORT${RESET}."
  echo "     (Add --public to expose VNC directly instead.)"
fi
echo
echo "  Stop it with: ${BOLD}./vnc.sh --stop${RESET}"
echo "${BOLD}───────────────────────────────────────────────${RESET}"
