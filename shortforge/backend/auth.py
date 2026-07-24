"""Single-password auth with signed session cookies.

The password is hashed (PBKDF2) and stored in the DB. On first boot it is
seeded from DASHBOARD_PASSWORD; afterwards it can be changed from the UI.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, db

COOKIE_NAME = "sf_session"
MAX_AGE = 60 * 60 * 24 * 14  # 14 days

_serializer = URLSafeTimedSerializer(config.SESSION_SECRET, salt="sf-session")


def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def set_password(password: str) -> None:
    salt = os.urandom(16)
    db.set_setting("password_hash", _hash_password(password, salt))


def verify_password(password: str) -> bool:
    stored = db.get_setting("password_hash")
    if not stored or "$" not in stored:
        return False
    salt_hex, _ = stored.split("$", 1)
    candidate = _hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate, stored)


def ensure_password_seeded() -> None:
    if not db.get_setting("password_hash"):
        set_password(config.BOOTSTRAP_PASSWORD)


def issue_session() -> str:
    return _serializer.dumps({"ok": True, "ts": time.time()})


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=MAX_AGE)
        return True
    except BadSignature:
        return False
    except Exception:
        return False
