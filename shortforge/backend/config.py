"""Static configuration derived from environment variables.

Anything a user should be able to change at runtime (API keys, model names)
lives in the DB `settings` table instead — see db.get_setting / effective().
Only bootstrap values that must exist before the DB is opened live here.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
UPLOADS_DIR = DATA_DIR / "sources"
OUTPUT_DIR = DATA_DIR / "shorts"
WORK_DIR = DATA_DIR / "work"
DB_PATH = DATA_DIR / "shortforge.db"

FRONTEND_DIR = BASE_DIR / "frontend"

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

# Cookie signing secret. A weak default is tolerated for local dev but setup.sh
# always writes a strong random one into .env.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "shortforge-insecure-dev-secret")

# First-boot dashboard password. On first run it is hashed into the DB; after
# that the DB value wins so the password can be changed from the UI.
BOOTSTRAP_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "changeme")

# First-boot defaults for the runtime-editable settings.
ENV_DEFAULTS = {
    "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
    "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"),
    "whisper_model": os.environ.get("WHISPER_MODEL", "small"),
    "ngrok_authtoken": os.environ.get("NGROK_AUTHTOKEN", ""),
    "tts_engine": os.environ.get("TTS_ENGINE", "kokoro"),
    "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
    "google_client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    "public_base_url": os.environ.get("PUBLIC_BASE_URL", ""),
    "default_dub_music": os.environ.get("DEFAULT_DUB_MUSIC", ""),
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOADS_DIR, OUTPUT_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
