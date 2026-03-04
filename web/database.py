"""SQLite database setup and helpers."""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "seed_money.db")


def get_db(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path=None):
    """Create tables if they don't exist."""
    conn = get_db(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cached_ratings (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            year INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cached_picks (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            data_json TEXT NOT NULL,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cached_bracket (
            id INTEGER PRIMARY KEY,
            year INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'queued',
            config_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS refresh_log (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def get_latest_ratings(conn):
    """Get the most recent cached ratings."""
    row = conn.execute(
        "SELECT data_json FROM cached_ratings ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return json.loads(row["data_json"])
    return None


def get_latest_picks(conn):
    """Get the most recent cached pick percentages."""
    row = conn.execute(
        "SELECT data_json FROM cached_picks ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return json.loads(row["data_json"])
    return None


def get_latest_bracket(conn):
    """Get the most recent cached bracket data."""
    row = conn.execute(
        "SELECT data_json FROM cached_bracket ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return json.loads(row["data_json"])
    return None


def get_job(conn, job_id):
    """Get a job by ID."""
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_queue_position(conn, job_id):
    """Get the queue position of a job (1-based, 0 if not queued)."""
    row = conn.execute(
        """SELECT COUNT(*) as pos FROM jobs
           WHERE status = 'queued'
           AND created_at <= (SELECT created_at FROM jobs WHERE id = ?)""",
        (job_id,)
    ).fetchone()
    return row["pos"] if row else 0


def get_team_list(conn):
    """Get list of team names from cached ratings (for autocomplete)."""
    ratings = get_latest_ratings(conn)
    if ratings:
        return sorted(ratings.keys())
    return []
