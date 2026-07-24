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

            -- COPY mode: tracked channels, their source shorts, and dubs.
            CREATE TABLE IF NOT EXISTS channels (
                id         TEXT PRIMARY KEY,
                url        TEXT,
                name       TEXT DEFAULT '',
                thumb      TEXT DEFAULT '',
                status     TEXT DEFAULT 'ready',
                message    TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS channel_shorts (
                id          TEXT PRIMARY KEY,
                channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                video_id    TEXT NOT NULL,
                title       TEXT DEFAULT '',
                url         TEXT DEFAULT '',
                thumb       TEXT DEFAULT '',
                duration    REAL DEFAULT 0,
                published    TEXT DEFAULT '',
                created_at  REAL NOT NULL,
                UNIQUE(channel_id, video_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cshorts_channel ON channel_shorts(channel_id);
            CREATE TABLE IF NOT EXISTS dubs (
                id          TEXT PRIMARY KEY,
                short_id    TEXT NOT NULL REFERENCES channel_shorts(id) ON DELETE CASCADE,
                lang        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'queued',
                progress    INTEGER NOT NULL DEFAULT 0,
                stage       TEXT DEFAULT '',
                message     TEXT DEFAULT '',
                error       TEXT DEFAULT '',
                log         TEXT DEFAULT '',
                path        TEXT DEFAULT '',
                thumb       TEXT DEFAULT '',
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dubs_short ON dubs(short_id);

            -- User-created destination channels for translated videos.
            CREATE TABLE IF NOT EXISTS my_channels (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            -- Uploaded background-music tracks.
            CREATE TABLE IF NOT EXISTS music (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                path       TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        # Additive migrations for the dubs table (existing installs).
        _ensure_column(c, "dubs", "dest_channel_id", "TEXT DEFAULT ''")
        _ensure_column(c, "dubs", "title", "TEXT DEFAULT ''")
        _ensure_column(c, "dubs", "description", "TEXT DEFAULT ''")
        _ensure_column(c, "dubs", "tags", "TEXT DEFAULT ''")
        _ensure_column(c, "dubs", "tr_title", "TEXT DEFAULT ''")
        _ensure_column(c, "dubs", "tr_description", "TEXT DEFAULT ''")
        _ensure_column(c, "dubs", "music_id", "TEXT DEFAULT ''")
        c.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


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


def delete_job_shorts(job_id: str) -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM shorts WHERE job_id=?", (job_id,))
        c.commit()


def reset_job(job_id: str) -> None:
    update_job(job_id, status="queued", progress=0, stage="", message="",
               error="", num_shorts=0)


def list_failed_jobs() -> list[dict]:
    with _lock:
        rows = _connect().execute("SELECT * FROM jobs WHERE status='error'").fetchall()
    return [_job_to_dict(r) for r in rows]


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


# --- channels (COPY mode) ---------------------------------------------------

def create_channel(url: str) -> str:
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO channels(id, url, status, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            (cid, url, "fetching", now, now),
        )
        c.commit()
    return cid


def update_channel(channel_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        c = _connect()
        c.execute(f"UPDATE channels SET {cols} WHERE id=?", (*fields.values(), channel_id))
        c.commit()


def get_channel(channel_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM channels WHERE id=?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def list_channels() -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM channels ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        with _lock:
            cnt = _connect().execute(
                "SELECT COUNT(*) AS n FROM channel_shorts WHERE channel_id=?", (d["id"],)
            ).fetchone()["n"]
        d["short_count"] = cnt
        out.append(d)
    return out


def delete_channel(channel_id: str) -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM channels WHERE id=?", (channel_id,))
        c.commit()


def upsert_channel_short(channel_id: str, video_id: str, **fields: Any) -> tuple[str, bool]:
    """Insert a channel short if new. Returns (id, is_new)."""
    with _lock:
        c = _connect()
        row = c.execute(
            "SELECT id FROM channel_shorts WHERE channel_id=? AND video_id=?",
            (channel_id, video_id),
        ).fetchone()
        if row:
            return row["id"], False
        sid = uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO channel_shorts(id, channel_id, video_id, title, url, "
            "thumb, duration, published, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                sid, channel_id, video_id,
                fields.get("title", ""), fields.get("url", ""),
                fields.get("thumb", ""), fields.get("duration", 0),
                fields.get("published", ""), time.time(),
            ),
        )
        c.commit()
    return sid, True


def list_channel_shorts(channel_id: str) -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM channel_shorts WHERE channel_id=? ORDER BY created_at DESC",
            (channel_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        with _lock:
            dubs = _connect().execute(
                "SELECT id, lang, status, progress FROM dubs WHERE short_id=? "
                "ORDER BY created_at DESC", (d["id"],)
            ).fetchall()
        d["dubs"] = [dict(x) for x in dubs]
        result.append(d)
    return result


def get_channel_short(short_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM channel_shorts WHERE id=?", (short_id,)
        ).fetchone()
    return dict(row) if row else None


# --- dubs -------------------------------------------------------------------

def create_dub(short_id: str, lang: str, dest_channel_id: str = "", music_id: str = "") -> str:
    did = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO dubs(id, short_id, lang, status, dest_channel_id, "
            "music_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (did, short_id, lang, "queued", dest_channel_id, music_id, now, now),
        )
        c.commit()
    return did


def reset_dub(dub_id: str) -> None:
    update_dub(dub_id, status="queued", progress=0, stage="", message="", error="")


def delete_dub(dub_id: str) -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM dubs WHERE id=?", (dub_id,))
        c.commit()


def update_dub(dub_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        c = _connect()
        c.execute(f"UPDATE dubs SET {cols} WHERE id=?", (*fields.values(), dub_id))
        c.commit()


def append_dub_log(dub_id: str, line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with _lock:
        c = _connect()
        row = c.execute("SELECT log FROM dubs WHERE id=?", (dub_id,)).fetchone()
        prev = (row["log"] if row else "") or ""
        lines = (prev + f"[{stamp}] {line}\n").splitlines()[-200:]
        c.execute(
            "UPDATE dubs SET log=?, updated_at=? WHERE id=?",
            ("\n".join(lines) + "\n", time.time(), dub_id),
        )
        c.commit()


def get_dub(dub_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute("SELECT * FROM dubs WHERE id=?", (dub_id,)).fetchone()
    return dict(row) if row else None


def next_queued_dub() -> Optional[dict]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM dubs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def list_all_dubs_in_progress() -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM dubs WHERE status NOT IN ('done','error')"
        ).fetchall()
    return [dict(r) for r in rows]


# --- my channels (dubbing destinations) -------------------------------------

def create_my_channel(name: str) -> str:
    cid = uuid.uuid4().hex[:12]
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO my_channels(id, name, created_at) VALUES(?,?,?)",
            (cid, name, time.time()),
        )
        c.commit()
    return cid


def list_my_channels() -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM my_channels ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        with _lock:
            cnt = _connect().execute(
                "SELECT COUNT(*) AS n FROM dubs WHERE dest_channel_id=? AND status='done'",
                (d["id"],),
            ).fetchone()["n"]
        d["video_count"] = cnt
        out.append(d)
    return out


def get_my_channel(channel_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM my_channels WHERE id=?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_my_channel(channel_id: str) -> None:
    with _lock:
        c = _connect()
        # Detach dubs from this destination (keep the dubs themselves).
        c.execute("UPDATE dubs SET dest_channel_id='' WHERE dest_channel_id=?", (channel_id,))
        c.execute("DELETE FROM my_channels WHERE id=?", (channel_id,))
        c.commit()


def list_dubs_for_dest(channel_id: str) -> list[dict]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM dubs WHERE dest_channel_id=? ORDER BY updated_at DESC",
            (channel_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- music library ----------------------------------------------------------

def add_music(name: str, path: str) -> str:
    mid = uuid.uuid4().hex[:12]
    with _lock:
        c = _connect()
        c.execute("INSERT INTO music(id, name, path, created_at) VALUES(?,?,?,?)",
                  (mid, name, path, time.time()))
        c.commit()
    return mid


def list_music() -> list[dict]:
    with _lock:
        rows = _connect().execute("SELECT * FROM music ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_music(music_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute("SELECT * FROM music WHERE id=?", (music_id,)).fetchone()
    return dict(row) if row else None


def delete_music(music_id: str) -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM music WHERE id=?", (music_id,))
        c.commit()
