#!/usr/bin/env bash
# ShortForge — launcher.
#   ./run.sh            start the dashboard on $HOST:$PORT (from .env)
#   ./run.sh --tunnel   also open a public ngrok tunnel and print the URL
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "venv/ not found. Run ./setup.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source venv/bin/activate

# Load .env into the environment.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
TUNNEL=0
[ "${1:-}" = "--tunnel" ] && TUNNEL=1

if [ "$TUNNEL" -eq 1 ]; then
  echo "Opening ngrok tunnel on port $PORT ..."
  # Starts ngrok via pyngrok, prints the public URL, and keeps it open for the
  # lifetime of the server process.
  python3 - "$PORT" <<'PY' &
import sys, time
from pyngrok import ngrok, conf
import os
port = sys.argv[1]
token = os.environ.get("NGROK_AUTHTOKEN")
if token:
    conf.get_default().auth_token = token
tunnel = ngrok.connect(port, "http")
print("\n============================================================")
print(f"  Public dashboard URL:  {tunnel.public_url}")
print("============================================================\n", flush=True)
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    ngrok.disconnect(tunnel.public_url)
PY
  sleep 2
fi

echo "Starting ShortForge on http://$HOST:$PORT"
exec uvicorn backend.main:app --host "$HOST" --port "$PORT"
