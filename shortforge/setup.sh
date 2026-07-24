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
if command -v apt-get >/dev/null 2>&1; then
  info "Installing system packages (ffmpeg, python3-venv, git)..."
  SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -y
  $SUDO apt-get install -y ffmpeg python3 python3-venv python3-pip git fonts-dejavu-core
else
  warn "apt-get not found. Make sure ffmpeg and python3 (>=3.10) are installed."
fi

# --- 2. Python virtual environment -----------------------------------------
if [ ! -d venv ]; then
  info "Creating Python virtual environment (venv/)..."
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

info "Upgrading pip and installing Python dependencies (this can take a few minutes)..."
pip install --upgrade pip wheel
pip install -r requirements.txt

# --- 3. .env ----------------------------------------------------------------
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
