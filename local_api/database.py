"""Phase 4.4 — SQLite connection pool + schema initialisation (sync tables)."""

import sqlite3
import threading
from pathlib import Path

from .config import DB_PATH

# Thread-local connection for safe concurrent access
_local = threading.local()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id              TEXT PRIMARY KEY,
    title                TEXT    NOT NULL,
    description          TEXT,
    priority             TEXT    NOT NULL DEFAULT 'P2'
                         CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
    status               TEXT    NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'completed', 'cancelled')),
    start_time           TEXT,
    due_time             TEXT,
    timezone             TEXT    NOT NULL DEFAULT 'Asia/Shanghai',
    location             TEXT,
    need_weather_check   INTEGER NOT NULL DEFAULT 0,
    reminder_channels    TEXT    NOT NULL DEFAULT '["local_ui"]',
    created_channel      TEXT    NOT NULL DEFAULT 'api_test'
                         CHECK (created_channel IN ('local_ui', 'wechat', 'hermes', 'api_test')),
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    sync_enabled         INTEGER NOT NULL DEFAULT 0,
    last_sync_status     TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    sync_id             TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    sync_target         TEXT NOT NULL
                        CHECK (sync_target IN ('apple_calendar', 'apple_reminder')),
    sync_key            TEXT NOT NULL UNIQUE,
    payload_hash        TEXT,
    sync_status         TEXT NOT NULL DEFAULT 'pending'
                        CHECK (sync_status IN ('pending', 'in_progress', 'synced', 'failed', 'failed_permanent', 'skipped', 'stale', 'orphaned', 'disabled', 'deleted')),
    sync_version        INTEGER NOT NULL DEFAULT 1,
    external_id         TEXT,
    last_synced_at      TEXT,
    last_sync_trigger   TEXT,
    started_at          TEXT,
    locked_at           TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sync_state_task_id ON sync_state(task_id);
CREATE INDEX IF NOT EXISTS idx_sync_state_target ON sync_state(sync_target);
CREATE INDEX IF NOT EXISTS idx_sync_state_status ON sync_state(sync_status);

CREATE TABLE IF NOT EXISTS sync_logs (
    log_id              TEXT PRIMARY KEY,
    sync_id             TEXT NOT NULL,
    local_task_id       TEXT,
    sync_target         TEXT NOT NULL
                        CHECK (sync_target IN ('apple_calendar', 'apple_reminder')),
    sync_attempt        INTEGER NOT NULL DEFAULT 1,
    sync_result         TEXT NOT NULL
                        CHECK (sync_result IN ('success', 'skipped', 'failed', 'drift_detected')),
    error_code          TEXT,
    error_message       TEXT,
    drift_detected      INTEGER NOT NULL DEFAULT 0,
    drift_fields        TEXT,
    payload_hash_before TEXT,
    payload_hash_after  TEXT,
    external_id_before  TEXT,
    external_id_after   TEXT,
    request_id          TEXT,
    triggered_by        TEXT NOT NULL DEFAULT 'api',
    created_at          TEXT NOT NULL,
    FOREIGN KEY (sync_id) REFERENCES sync_state(sync_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sync_logs_sync_id ON sync_logs(sync_id);
CREATE INDEX IF NOT EXISTS idx_sync_logs_target ON sync_logs(sync_target);
CREATE INDEX IF NOT EXISTS idx_sync_logs_result ON sync_logs(sync_result);
"""


_SYNC_STATE_MIGRATIONS = [
    "ALTER TABLE sync_state ADD COLUMN started_at TEXT",
    "ALTER TABLE sync_state ADD COLUMN locked_at TEXT",
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run schema migrations safely — handles existing columns gracefully."""
    for ddl in _SYNC_STATE_MIGRATIONS:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def get_db() -> sqlite3.Connection:
    """Return a thread-local SQLite connection with WAL mode enabled.

    The connection is lazily created per thread and cached.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        _run_migrations(conn)
        conn.commit()
        _local.conn = conn
    return conn


def init_db() -> None:
    """Ensure the database and schema exist. Idempotent — safe to call multiple times."""
    conn = get_db()
    conn.executescript(SCHEMA_SQL)
    _run_migrations(conn)
    conn.commit()


def close_db() -> None:
    """Close the thread-local connection (used during shutdown / testing)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def reset_db() -> None:
    """Drop and recreate all tables — ONLY for test/dev use."""
    conn = get_db()
    conn.execute("DROP TABLE IF EXISTS sync_logs")
    conn.execute("DROP TABLE IF EXISTS sync_state")
    conn.execute("DROP TABLE IF EXISTS tasks")
    conn.executescript(SCHEMA_SQL)
    _run_migrations(conn)
    conn.commit()
