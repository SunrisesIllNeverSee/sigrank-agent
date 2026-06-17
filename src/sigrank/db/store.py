"""db/store.py — local SQLite store (stdlib sqlite3, no driver dependency).

Tables (per the group brief):
  sources           — registered log sources (claude-code, chatgpt, ...)
  sessions          — one conversation/session per source
  messages          — individual messages with token telemetry
  feature_message   — per-message derived features (compression inputs, etc.)
  feature_session   — per-session aggregates (turns, depth, active minutes)
  snapshot_local    — locally computed snapshots (one per window/compute run)
  publish_log       — append-only record of publish attempts + server responses
  settings          — small key/value store for agent state (e.g. last scan)

All writes go through a single Store instance. The store NEVER reads the wall
clock itself — timestamps come from the parsed telemetry or are passed in by the
caller, keeping computation deterministic and replayable.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

# Schema is created idempotently via CREATE TABLE IF NOT EXISTS, so re-opening an
# existing db is safe. Bump SCHEMA_USER_VERSION on a breaking change.
SCHEMA_USER_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id   TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    path        TEXT NOT NULL,
    label       TEXT,
    added_at    TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    platform     TEXT,
    started_at   TEXT,
    ended_at     TEXT,
    turns        INTEGER NOT NULL DEFAULT 0,
    raw_path     TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id            TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL,
    role                  TEXT,
    model                 TEXT,
    ts                    TEXT,
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    char_len              INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

CREATE TABLE IF NOT EXISTS feature_message (
    message_id   TEXT PRIMARY KEY,
    feature_json TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(message_id)
);

CREATE TABLE IF NOT EXISTS feature_session (
    session_id   TEXT PRIMARY KEY,
    feature_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS snapshot_local (
    snapshot_id  TEXT PRIMARY KEY,
    window_type  TEXT NOT NULL,
    window_start TEXT,
    window_end   TEXT,
    computed_at  TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    snapshot_hash TEXT
);

CREATE TABLE IF NOT EXISTS publish_log (
    publish_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status       TEXT NOT NULL,
    http_status  INTEGER,
    response_json TEXT,
    signature    TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshot_local(snapshot_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    """Thin SQLite wrapper. Open with a path; call init_schema() once."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    # ── lifecycle ───────────────────────────────────────────────────────────-

    def init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(_SCHEMA)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── sources ───────────────────────────────────────────────────────────--

    def add_source(
        self, source_id: str, source_type: str, path: str, label: str, added_at: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO sources "
                "(source_id, source_type, path, label, added_at, enabled) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (source_id, source_type, path, label, added_at),
            )

    def list_sources(self) -> list[sqlite3.Row]:
        with closing(self.conn.execute("SELECT * FROM sources ORDER BY added_at")) as cur:
            return cur.fetchall()

    def remove_source(self, source_id: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM sources WHERE source_id = ? OR label = ?",
                (source_id, source_id),
            )
            return cur.rowcount

    def get_source(self, source_id: str) -> sqlite3.Row | None:
        with closing(
            self.conn.execute(
                "SELECT * FROM sources WHERE source_id = ? OR label = ?",
                (source_id, source_id),
            )
        ) as cur:
            return cur.fetchone()

    # ── sessions + messages ───────────────────────────────────────────────--

    def upsert_session(
        self,
        session_id: str,
        source_id: str,
        platform: str | None,
        started_at: str | None,
        ended_at: str | None,
        turns: int,
        raw_path: str | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(session_id, source_id, platform, started_at, ended_at, turns, raw_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, source_id, platform, started_at, ended_at, turns, raw_path),
            )

    def upsert_messages(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO messages "
                "(message_id, session_id, role, model, ts, input_tokens, output_tokens, "
                " cache_read_tokens, cache_creation_tokens, char_len) "
                "VALUES (:message_id, :session_id, :role, :model, :ts, :input_tokens, "
                " :output_tokens, :cache_read_tokens, :cache_creation_tokens, :char_len)",
                rows,
            )
        return len(rows)

    def messages_in_window(
        self, start: str | None, end: str | None
    ) -> list[sqlite3.Row]:
        """Return messages whose ts falls in [start, end). None bounds are open."""
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start)
        if end is not None:
            clauses.append("ts < ?")
            params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM messages{where} ORDER BY ts"
        with closing(self.conn.execute(sql, params)) as cur:
            return cur.fetchall()

    def sessions_in_window(
        self, start: str | None, end: str | None
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("(started_at IS NULL OR started_at >= ?)")
            params.append(start)
        if end is not None:
            clauses.append("(started_at IS NULL OR started_at < ?)")
            params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM sessions{where} ORDER BY started_at"
        with closing(self.conn.execute(sql, params)) as cur:
            return cur.fetchall()

    def all_messages(self) -> list[sqlite3.Row]:
        with closing(self.conn.execute("SELECT * FROM messages ORDER BY ts")) as cur:
            return cur.fetchall()

    def all_sessions(self) -> list[sqlite3.Row]:
        with closing(self.conn.execute("SELECT * FROM sessions")) as cur:
            return cur.fetchall()

    # ── features ──────────────────────────────────────────────────────────--

    def set_message_feature(self, message_id: str, feature: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO feature_message (message_id, feature_json) "
                "VALUES (?, ?)",
                (message_id, json.dumps(feature, sort_keys=True)),
            )

    def set_session_feature(self, session_id: str, feature: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO feature_session (session_id, feature_json) "
                "VALUES (?, ?)",
                (session_id, json.dumps(feature, sort_keys=True)),
            )

    # ── snapshots ─────────────────────────────────────────────────────────--

    def save_snapshot(
        self,
        snapshot_id: str,
        window_type: str,
        window_start: str | None,
        window_end: str | None,
        computed_at: str,
        payload: dict[str, Any],
        snapshot_hash: str | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO snapshot_local "
                "(snapshot_id, window_type, window_start, window_end, computed_at, "
                " payload_json, snapshot_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    window_type,
                    window_start,
                    window_end,
                    computed_at,
                    json.dumps(payload, sort_keys=True),
                    snapshot_hash,
                ),
            )

    def latest_snapshot(self, window_type: str | None = None) -> sqlite3.Row | None:
        if window_type:
            sql = (
                "SELECT * FROM snapshot_local WHERE window_type = ? "
                "ORDER BY computed_at DESC LIMIT 1"
            )
            params: tuple[str, ...] = (window_type,)
        else:
            sql = "SELECT * FROM snapshot_local ORDER BY computed_at DESC LIMIT 1"
            params = ()
        with closing(self.conn.execute(sql, params)) as cur:
            return cur.fetchone()

    def get_snapshot(self, snapshot_id: str) -> sqlite3.Row | None:
        with closing(
            self.conn.execute(
                "SELECT * FROM snapshot_local WHERE snapshot_id = ?", (snapshot_id,)
            )
        ) as cur:
            return cur.fetchone()

    # ── publish log ───────────────────────────────────────────────────────--

    def log_publish(
        self,
        snapshot_id: str,
        attempted_at: str,
        status: str,
        http_status: int | None,
        response: dict[str, Any] | None,
        signature: str | None,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO publish_log "
                "(snapshot_id, attempted_at, status, http_status, response_json, signature) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    attempted_at,
                    status,
                    http_status,
                    json.dumps(response, sort_keys=True) if response is not None else None,
                    signature,
                ),
            )
            return int(cur.lastrowid or 0)

    def publish_history(self, limit: int = 20) -> list[sqlite3.Row]:
        with closing(
            self.conn.execute(
                "SELECT * FROM publish_log ORDER BY publish_id DESC LIMIT ?", (limit,)
            )
        ) as cur:
            return cur.fetchall()

    # ── key/value settings ──────────────────────────────────────────────────-

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_setting(self, key: str) -> str | None:
        with closing(
            self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        ) as cur:
            row = cur.fetchone()
            return row["value"] if row else None


def open_store(path: Path | str, *, init: bool = False) -> Store:
    """Open (and optionally initialize) the store at `path`."""
    store = Store(path)
    if init:
        store.init_schema()
    return store
