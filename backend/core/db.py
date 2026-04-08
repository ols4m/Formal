"""
Shared SQLite database layer for Formal.
All apps read/write through this module.

DB location: backend/formal.db
"""

import sqlite3
import os
from pathlib import Path

# Resolve DB path relative to this file so it's stable regardless of CWD
DB_PATH = Path(__file__).parent.parent / "formal.db"


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
            -- One row per course per quarter per scrape run
            CREATE TABLE IF NOT EXISTS grades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at   TEXT NOT NULL,
                course       TEXT NOT NULL,
                course_type  TEXT DEFAULT 'Regular',
                quarter      TEXT NOT NULL,
                letter_grade TEXT,
                numeric_grade REAL
            );

            -- One row per assignment per course per quarter
            CREATE TABLE IF NOT EXISTS assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at  TEXT NOT NULL,
                course      TEXT NOT NULL,
                quarter     TEXT NOT NULL DEFAULT 'Q2',
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
                status        TEXT DEFAULT 'pending'
            );
        """)
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
