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
    espeak-ng xvfb x11vnc novnc
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

info "Setup complete."
echo
echo -e "  Next steps:"
echo -e "    1. ${BOLD}nano .env${RESET}                  # set DASHBOARD_PASSWORD (and optionally API keys)"
echo -e "    2. ${BOLD}./run.sh${RESET}                    # start the dashboard"
echo -e "       ${BOLD}./run.sh --tunnel${RESET}           # start + expose a public ngrok URL"
echo
