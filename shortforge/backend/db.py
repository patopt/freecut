"""Thin SQLite data layer (no ORM) shared by the API and the worker thread.

A single connection is opened with check_same_thread=False and guarded by a
lock, which is plenty for one worker + a handful of dashboard requests.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from . import config

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db() -> None:
    with _lock:
        c = _connect()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id         TEXT PRIMARY KEY,
                url        TEXT,
                title      TEXT,
                status     TEXT NOT NULL DEFAULT 'queued',
                progress   INTEGER NOT NULL DEFAULT 0,
                stage      TEXT DEFAULT '',
                message    TEXT DEFAULT '',
                error      TEXT DEFAULT '',
                log        TEXT DEFAULT '',
                params     TEXT DEFAULT '{}',
                source_path TEXT DEFAULT '',
                duration   REAL DEFAULT 0,
                num_shorts INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shorts (
                id         TEXT PRIMARY KEY,
                job_id     TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                idx        INTEGER NOT NULL,
                title      TEXT DEFAULT '',
                reason     TEXT DEFAULT '',
                score      REAL DEFAULT 0,
                start      REAL DEFAULT 0,
                end        REAL DEFAULT 0,
                path       TEXT DEFAULT '',
                thumb      TEXT DEFAULT '',
                width      INTEGER DEFAULT 0,
                height     INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_shorts_job ON shorts(job_id);
            """
        )
        c.commit()


# --- settings ---------------------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        row = _connect().execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: Any) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, "" if value is None else str(value)),
        )
        c.commit()


def effective(key: str) -> str:
    """Runtime setting: DB value if present, else the env/bootstrap default."""
    val = get_setting(key)
    if val is None or val == "":
        return config.ENV_DEFAULTS.get(key, "")
    return val


def seed_defaults() -> None:
    """Populate settings that have no DB row yet from env defaults."""
    for key, val in config.ENV_DEFAULTS.items():
        if get_setting(key) is None and val:
            set_setting(key, val)


# --- jobs -------------------------------------------------------------------

def create_job(url: str, title: str, params: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO jobs(id, url, title, status, progress, params, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (jid, url, title, "queued", 0, json.dumps(params), now, now),
        )
        c.commit()
    return jid


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        c = _connect()
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        c.commit()


def append_log(job_id: str, line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with _lock:
        c = _connect()
        row = c.execute("SELECT log FROM jobs WHERE id=?", (job_id,)).fetchone()
        prev = (row["log"] if row else "") or ""
        # Keep the log bounded to the last ~200 lines.
        lines = (prev + f"[{stamp}] {line}\n").splitlines()[-200:]
        c.execute(
            "UPDATE jobs SET log=?, updated_at=? WHERE id=?",
            ("\n".join(lines) + "\n", time.time(), job_id),
        )
        c.commit()


def _job_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["params"] = json.loads(d.get("params") or "{}")
    except (ValueError, TypeError):
        d["params"] = {}
    return d


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_to_dict(row) if row else None


def list_jobs(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_job_to_dict(r) for r in rows]


def next_queued_job() -> Optional[dict]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return _job_to_dict(row) if row else None


def delete_job(job_id: str) -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        c.commit()


# --- shorts -----------------------------------------------------------------

def add_short(job_id: str, idx: int, **fields: Any) -> str:
    sid = uuid.uuid4().hex[:12]
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO shorts(id, job_id, idx, title, reason, score, start, "
            "end, path, thumb, width, height, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, job_id, idx,
                fields.get("title", ""), fields.get("reason", ""),
                fields.get("score", 0), fields.get("start", 0),
                fields.get("end", 0), fields.get("path", ""),
                fields.get("thumb", ""), fields.get("width", 0),
                fields.get("height", 0), time.time(),
            ),
        )
        c.commit()
    return sid


def list_shorts(job_id: str) -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM shorts WHERE job_id=? ORDER BY idx ASC", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_short(short_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM shorts WHERE id=?", (short_id,)
        ).fetchone()
    return dict(row) if row else None
