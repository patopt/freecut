#!/usr/bin/env bash
# ShortForge — one-shot installer for Ubuntu/Debian VPS.
# Installs system deps (ffmpeg), a Python venv, all Python packages, and
# writes a .env with a strong random session secret. Run once after cloning.
set -euo pipefail

cd "$(dirname "$0")"

BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RESET="\033[0m"
info()  { echo -e "${BOLD}${GREEN}==>${RESET} $*"; }
warn()  { echo -e "${BOLD}${YELLOW}!! ${RESET} $*"; }

# --- 1. System packages -----------------------------------------------------
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if command -v apt-get >/dev/null 2>&1; then
  info "Installing system packages (ffmpeg, fonts, git)..."
  $SUDO apt-get update -y
  # xvfb/x11vnc/novnc power the in-dashboard remote browser used to log into
  # TikTok on the VPS; espeak-ng is required by the Kokoro voice engine.
  $SUDO apt-get install -y ffmpeg git fonts-dejavu-core software-properties-common \
    espeak-ng xvfb x11vnc curl x11-utils x11-xserver-utils
  # novnc isn't in every release's repos — don't let it fail the whole install.
  $SUDO apt-get install -y novnc || warn "novnc package unavailable; will download it instead"
  # Desktop for the dashboard's Cloud tab. XFCE core only — the full xfce4
  # metapackage drags in goodies this box does not need. Fluxbox is the
  # fallback when XFCE is unavailable; the app detects whichever is installed.
  if ! $SUDO apt-get install -y xfce4-session xfwm4 xfce4-panel xfdesktop4 \
       thunar xfce4-terminal dbus-x11 mousepad; then
    warn "XFCE unavailable; falling back to a lightweight desktop"
    $SUDO apt-get install -y fluxbox tint2 pcmanfm xterm \
      || warn "No desktop installed; the Cloud tab will be unavailable"
  fi
  # xrdp lets ./vnc.sh expose the same remote browser over RDP. Its config is
  # written by vnc.sh, so keep the service down until that script sets it up.
  if $SUDO apt-get install -y xrdp; then
    $SUDO systemctl disable --now xrdp xrdp-sesman >/dev/null 2>&1 || true
  else
    warn "xrdp unavailable; ./vnc.sh will install it on demand"
  fi
else
  warn "apt-get not found. Make sure ffmpeg and Python 3.10-3.12 are installed."
fi

# --- 2. Pick a compatible Python (3.10-3.12) -------------------------------
# The ML wheels (ctranslate2, onnxruntime, opencv, pydantic-core) do not yet
# ship prebuilt binaries for Python 3.13+, and building them from source fails
# (this is the pyo3 "interpreter version 3.14 is newer than PyO3's maximum"
# error). So we require 3.10-3.12 and install 3.12 via deadsnakes if needed.
PY=""
for cand in python3.12 python3.11 python3.10; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done

if [ -z "$PY" ] && command -v apt-get >/dev/null 2>&1; then
  info "No suitable Python found — installing Python 3.12 (deadsnakes PPA)..."
  $SUDO add-apt-repository -y ppa:deadsnakes/ppa || true
  $SUDO apt-get update -y
  $SUDO apt-get install -y python3.12 python3.12-venv python3.12-dev
  PY="python3.12"
fi

if [ -z "$PY" ]; then
  warn "Could not find or install Python 3.10-3.12. Install it manually and re-run."
  exit 1
fi
info "Using $PY ($($PY --version 2>&1))"

# --- 3. Python virtual environment -----------------------------------------
# Recreate the venv if it exists but uses an unsupported Python version.
if [ -d venv ]; then
  CURV="$(venv/bin/python -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo none)"
  case "$CURV" in
    3.10|3.11|3.12) : ;;
    *) warn "Existing venv uses Python $CURV — recreating with $PY."; rm -rf venv ;;
  esac
fi
if [ ! -d venv ]; then
  info "Creating Python virtual environment (venv/)..."
  "$PY" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

info "Upgrading pip and installing Python dependencies (this can take a few minutes)..."
pip install --upgrade pip wheel
pip install -r requirements.txt

# Chromium for headless TikTok posting (browser mode). Non-fatal if it fails —
# only the browser-mode TikTok publishing needs it.
info "Installing Chromium for browser-mode TikTok posting..."
python -m playwright install --with-deps chromium || \
  warn "Chromium install failed — TikTok browser mode won't work until you run: python -m playwright install --with-deps chromium"

# --- 4. .env ----------------------------------------------------------------
if [ ! -f .env ]; then
  info "Creating .env from template..."
  cp .env.example .env
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  # Portable in-place edit (works on both GNU and BSD sed via a temp file).
  python3 - "$SECRET" <<'PY'
import re, sys, pathlib
secret = sys.argv[1]
p = pathlib.Path(".env")
txt = p.read_text()
txt = re.sub(r"^SESSION_SECRET=.*$", f"SESSION_SECRET={secret}", txt, flags=re.M)
p.write_text(txt)
PY
  warn "A .env was created. EDIT IT and set DASHBOARD_PASSWORD before launching!"
else
  info ".env already exists — leaving it untouched."
fi

mkdir -p data

# --- Swap: keeps long transcriptions from being OOM-killed -------------------
# Whisper on a long source can briefly spike past a 4 GB box. Swap turns a hard
# "Killed" into a slow moment instead of a lost job.
CURRENT_SWAP=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${CURRENT_SWAP:-0}" -lt 1024 ] && [ ! -f /swapfile ]; then
  info "Creating a 4 GB swapfile (protects long transcriptions from OOM)..."
  if $SUDO fallocate -l 4G /swapfile 2>/dev/null || \
     $SUDO dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none 2>/dev/null; then
    $SUDO chmod 600 /swapfile
    $SUDO mkswap /swapfile >/dev/null 2>&1
    $SUDO swapon /swapfile 2>/dev/null && info "Swap enabled."
    grep -q '^/swapfile' /etc/fstab 2>/dev/null || \
      echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
  else
    warn "Could not create a swapfile — long videos may hit the OOM killer."
  fi
else
  info "Swap already present."
fi

# --- 4b. TiktokAutoUploader (requests-based TikTok posting) ------------------
# Needs Node.js for its signature generation.
if ! command -v node >/dev/null 2>&1; then
  info "Installing Node.js (required by TiktokAutoUploader)..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO bash - >/dev/null 2>&1 \
    && $SUDO apt-get install -y nodejs \
    || warn "Node.js install failed; TikTok posting may not work"
fi

mkdir -p vendor
if [ -d vendor/TiktokAutoUploader/.git ]; then
  info "Updating TiktokAutoUploader..."
  git -C vendor/TiktokAutoUploader pull --quiet || warn "Could not update TiktokAutoUploader"
else
  info "Cloning TiktokAutoUploader..."
  git clone --depth 1 https://github.com/makiisthenes/TiktokAutoUploader.git \
    vendor/TiktokAutoUploader || warn "Could not clone TiktokAutoUploader"
fi
if [ -f vendor/TiktokAutoUploader/requirements.txt ]; then
  info "Installing TiktokAutoUploader dependencies..."
  pip install -r vendor/TiktokAutoUploader/requirements.txt \
    || warn "Some TiktokAutoUploader deps failed"
fi
if command -v npm >/dev/null 2>&1; then
  # The signature generator lives in tiktok_uploader/tiktok-signature/ and MUST
  # have its own node_modules — installing only at the repo root leaves it
  # broken with MODULE_NOT_FOUND and uploads silently do nothing.
  for pkg in vendor/TiktokAutoUploader \
             vendor/TiktokAutoUploader/tiktok_uploader/tiktok-signature; do
    if [ -f "$pkg/package.json" ]; then
      info "Installing npm packages in $pkg ..."
      (cd "$pkg" && npm install --silent) || warn "npm install failed in $pkg"
    fi
  done
  SIG_DIR=vendor/TiktokAutoUploader/tiktok_uploader/tiktok-signature
  if [ -d "$SIG_DIR" ] && [ ! -d "$SIG_DIR/node_modules" ]; then
    warn "tiktok-signature has no node_modules — TikTok uploads will fail."
  fi
fi
mkdir -p vendor/TiktokAutoUploader/CookiesDir vendor/TiktokAutoUploader/VideosDirPath

# --- 5. NordVPN CLI (rotates the exit IP when YouTube/TikTok block us) -------
if command -v nordvpn >/dev/null 2>&1; then
  info "NordVPN CLI already installed."
elif command -v apt-get >/dev/null 2>&1; then
  info "Installing NordVPN CLI..."
  if curl -sSf https://downloads.nordcdn.com/apps/linux/install.sh -o /tmp/nordvpn-install.sh; then
    $SUDO sh /tmp/nordvpn-install.sh -n || warn "NordVPN install script failed"
    rm -f /tmp/nordvpn-install.sh
    # The CLI talks to nordvpnd over a group-restricted socket.
    $SUDO usermod -aG nordvpn "$(whoami)" 2>/dev/null || true
    $SUDO systemctl enable --now nordvpnd 2>/dev/null || true
    if command -v nordvpn >/dev/null 2>&1; then
      nordvpn set technology nordlynx >/dev/null 2>&1 || true
      nordvpn set killswitch disabled >/dev/null 2>&1 || true
      # Keep the dashboard reachable while the tunnel is up.
      nordvpn set lan-discovery enabled >/dev/null 2>&1 || true
      info "NordVPN CLI installed. Log in from the dashboard (Settings -> VPN)."
    fi
  else
    warn "Could not download the NordVPN installer — VPN rotation will be disabled."
  fi
fi

# --- 6. noVNC web client ----------------------------------------------------
# The dashboard serves this to show the remote browser. Use the distro package
# when present, otherwise fetch a release into data/novnc.
if [ -f /usr/share/novnc/vnc.html ] || [ -f /usr/share/novnc/vnc_lite.html ]; then
  info "noVNC found at /usr/share/novnc"
elif [ -f data/novnc/vnc.html ]; then
  info "noVNC already downloaded in data/novnc"
else
  info "Downloading noVNC client..."
  if curl -fsSL https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz -o /tmp/novnc.tgz; then
    rm -rf data/novnc && mkdir -p data/novnc
    tar -xzf /tmp/novnc.tgz -C data/novnc --strip-components=1
    rm -f /tmp/novnc.tgz
    info "noVNC installed into data/novnc"
  else
    warn "Could not download noVNC — the in-dashboard remote browser won't display."
  fi
fi

info "Setup complete."
echo
echo -e "  Next steps:"
echo -e "    1. ${BOLD}nano .env${RESET}                  # set DASHBOARD_PASSWORD (and optionally API keys)"
echo -e "    2. ${BOLD}./run.sh${RESET}                    # start the dashboard"
echo -e "       ${BOLD}./run.sh --tunnel${RESET}           # start + expose a public ngrok URL"
echo
