"""
Shared SQLite database layer for Formal.
All apps read/write through this module.

DB location: backend/formal.db
"""

import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from core.profile import get_term_for_date

# Resolve DB path relative to this file so it's stable regardless of CWD
DB_PATH = BACKEND_DIR / "formal.db"


def get_db() -> sqlite3.Connection:
    """Return a connection to the shared DB with row_factory set."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    conn = get_db()
    with conn:
        conn.executescript("""
            -- One row per course per term and source period per scrape run
            CREATE TABLE IF NOT EXISTS grades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at   TEXT NOT NULL,
                course       TEXT NOT NULL,
                course_type  TEXT DEFAULT 'Regular',
                term         TEXT NOT NULL,
                source_period TEXT,
                letter_grade TEXT,
                numeric_grade REAL
            );

            -- One row per assignment per course and source period
            CREATE TABLE IF NOT EXISTS assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at  TEXT NOT NULL,
                course      TEXT NOT NULL,
                term        TEXT NOT NULL,
                source_period TEXT,
                name        TEXT NOT NULL,
                category    TEXT,
                due_date    TEXT,
                earned      REAL,
                possible    REAL,
                percent     REAL,
                letter      TEXT,
                flags       TEXT,
                source      TEXT DEFAULT 'powerschool'
            );

            -- Tasks for The Agenda (manual or sourced from grades/future integrations)
            CREATE TABLE IF NOT EXISTS tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source        TEXT NOT NULL DEFAULT 'manual',
                source_id     INTEGER,
                title         TEXT NOT NULL,
                course        TEXT,
                category      TEXT,
                due_date      TEXT,
                possible_points REAL DEFAULT 100,
                priority_score  REAL,
                tier          TEXT,
                days_left     INTEGER,
                impact        REAL,
                status        TEXT DEFAULT 'pending',
                notes         TEXT
            );

            -- Canonical source-neutral pool for all Formal items.
            CREATE TABLE IF NOT EXISTS items (
                id              TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                source_item_id  TEXT,
                item_type       TEXT NOT NULL CHECK (
                    item_type IN ('task', 'event', 'note', 'transaction', 'file', 'record')
                ),
                title           TEXT NOT NULL,
                description     TEXT,
                due_at          TEXT,
                importance      INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
                effort_minutes  REAL CHECK (effort_minutes IS NULL OR effort_minutes >= 0),
                consequence     INTEGER NOT NULL DEFAULT 3 CHECK (consequence BETWEEN 1 AND 5),
                status          TEXT NOT NULL DEFAULT 'open',
                raw_payload     TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_external_id
                ON items (source, source_item_id)
                WHERE source_item_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_items_status_due
                ON items (status, due_at);
        """)
        _migrate_period_columns(conn, "grades")
        _migrate_period_columns(conn, "assignments")
        _migrate_items_type_constraint(conn)
    conn.close()


def _migrate_period_columns(
    conn: sqlite3.Connection,
    table: str,
    profile: dict | None = None,
) -> None:
    """Rename legacy quarter columns and retain their labels as source periods."""
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    migrated_legacy_column = "quarter" in columns and "term" not in columns
    if migrated_legacy_column:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN quarter TO term")
        columns.remove("quarter")
        columns.add("term")
    if "source_period" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN source_period TEXT")
        columns.add("source_period")
        if "term" in columns:
            conn.execute(
                f"UPDATE {table} SET source_period = term WHERE source_period IS NULL"
            )
    if migrated_legacy_column and "scraped_at" in columns and "source_period" in columns:
        rows = conn.execute(
            f"SELECT id, scraped_at FROM {table} WHERE source_period IS NOT NULL"
        ).fetchall()
        for row in rows:
            term = get_term_for_date(row["scraped_at"], profile)
            if term:
                conn.execute(
                    f"UPDATE {table} SET term = ? WHERE id = ?",
                    (term["name"], row["id"]),
                )


def _migrate_items_type_constraint(conn: sqlite3.Connection) -> None:
    """Rebuild items table if its CHECK constraint doesn't include 'record'."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()
    if row is None or "'record'" in row["sql"]:
        return  # table doesn't exist yet, or already up to date

    conn.executescript("""
        PRAGMA foreign_keys=OFF;

        CREATE TABLE items_new (
            id              TEXT PRIMARY KEY,
            source          TEXT NOT NULL,
            source_item_id  TEXT,
            item_type       TEXT NOT NULL CHECK (
                item_type IN ('task', 'event', 'note', 'transaction', 'file', 'record')
            ),
            title           TEXT NOT NULL,
            description     TEXT,
            due_at          TEXT,
            importance      INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
            effort_minutes  REAL CHECK (effort_minutes IS NULL OR effort_minutes >= 0),
            consequence     INTEGER NOT NULL DEFAULT 3 CHECK (consequence BETWEEN 1 AND 5),
            status          TEXT NOT NULL DEFAULT 'open',
            raw_payload     TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        INSERT INTO items_new SELECT * FROM items;
        DROP TABLE items;
        ALTER TABLE items_new RENAME TO items;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_external_id
            ON items (source, source_item_id)
            WHERE source_item_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_items_status_due
            ON items (status, due_at);

        PRAGMA foreign_keys=ON;
    """)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
