#!/usr/bin/env bash
# ShortForge — install a 24/7 systemd service so the app keeps running after you
# disconnect from SSH and restarts on reboot / crash. Run once.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
USER_NAME="$(whoami)"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found — this host doesn't use systemd." >&2
  echo "Alternative: run 'nohup ./run.sh --tunnel > shortforge.log 2>&1 &'." >&2
  exit 1
fi

echo "==> Installing systemd service (shortforge) for $DIR (user: $USER_NAME)…"
$SUDO tee /etc/systemd/system/shortforge.service >/dev/null <<EOF
[Unit]
Description=ShortForge video factory
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/run.sh --tunnel
Restart=always
RestartSec=5
User=$USER_NAME

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable shortforge

echo
echo "==> IMPORTANT: stop any manually-running ./run.sh first (Ctrl+C in its window),"
echo "    otherwise the service can't bind the port. Then start it with:"
echo
echo "      $SUDO systemctl start shortforge"
echo
echo "Useful commands:"
echo "  $SUDO systemctl status shortforge      # is it running?"
echo "  $SUDO journalctl -u shortforge -f      # live logs (incl. the ngrok URL)"
echo "  $SUDO systemctl restart shortforge     # restart"
echo "  $SUDO systemctl disable --now shortforge   # turn 24/7 off"
