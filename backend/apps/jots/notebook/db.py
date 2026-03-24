"""
Database module for Resources App.
Handles SQLite storage for all resources.
"""

import sqlite3
import os
from datetime import datetime
from pathlib import Path

# Database file location
DB_PATH = Path(__file__).parent / "resources.db"


def get_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


def init_db():
    """Initialize the database with the resources table."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT,
            type TEXT,
            platform TEXT,
            thumbnail TEXT,
            description TEXT,
            file_path TEXT,
            topics TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add topics column if it doesn't exist (for existing databases)
    try:
        cursor.execute("ALTER TABLE resources ADD COLUMN topics TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()


def add_resource(title, url=None, resource_type=None, platform=None,
                 thumbnail=None, description=None, file_path=None):
    """Add a new resource to the database."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO resources (title, url, type, platform, thumbnail, description, file_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (title, url, resource_type, platform, thumbnail, description, file_path))

    resource_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return resource_id


def get_all_resources():
    """Get all resources from the database."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM resources ORDER BY created_at DESC")
    rows = cursor.fetchall()

    conn.close()

    # Convert to list of dictionaries
    return [dict(row) for row in rows]


def get_resource_by_id(resource_id):
    """Get a single resource by ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM resources WHERE id = ?", (resource_id,))
    row = cursor.fetchone()

    conn.close()

    return dict(row) if row else None


def delete_resource(resource_id):
    """Delete a resource by ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def search_resources(query):
    """Search resources by title or description."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM resources
        WHERE title LIKE ? OR description LIKE ?
        ORDER BY created_at DESC
    """, (f"%{query}%", f"%{query}%"))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def update_topics(resource_id, topics):
    """Update topics for a resource. Topics is a list that gets stored as comma-separated."""
    conn = get_connection()
    cursor = conn.cursor()

    topics_str = ",".join(topics) if topics else None

    cursor.execute("""
        UPDATE resources SET topics = ? WHERE id = ?
    """, (topics_str, resource_id))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


# Initialize database when module is imported
init_db()
