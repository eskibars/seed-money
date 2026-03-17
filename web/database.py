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
    _ensure_column(conn, "cached_picks", "year", "INTEGER")
    conn.commit()
    conn.close()


def get_latest_ratings(conn, source=None, year=None):
    """Get the most recent cached ratings, optionally filtered by source/year."""
    query = "SELECT data_json FROM cached_ratings"
    clauses = []
    params = []

    if source:
        clauses.append("source = ?")
        params.append(source)
    if year is not None:
        clauses.append("year = ?")
        params.append(year)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY fetched_at DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    if row:
        return json.loads(row["data_json"])
    return None


def get_latest_picks(conn, year=None):
    """Get the most recent cached pick percentages."""
    query = "SELECT data_json FROM cached_picks"
    params = []
    if year is not None and _table_has_column(conn, "cached_picks", "year"):
        query += " WHERE year = ?"
        params.append(year)
    query += " ORDER BY fetched_at DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    if row:
        return json.loads(row["data_json"])
    return None


def get_pick_sources(conn, year=None):
    """Get the latest cached pick percentages for each source."""
    has_year = _table_has_column(conn, "cached_picks", "year")
    if year is not None and has_year:
        rows = conn.execute(
            """
            SELECT source, data_json, year, fetched_at
            FROM cached_picks
            WHERE year = ?
              AND id IN (
                  SELECT MAX(id)
                  FROM cached_picks
                  WHERE year = ?
                  GROUP BY source
              )
            ORDER BY fetched_at DESC
            """,
            (year, year),
        ).fetchall()
    else:
        select_year = ", year" if has_year else ""
        rows = conn.execute(
            f"""
            SELECT source, data_json{select_year}, fetched_at
            FROM cached_picks
            WHERE id IN (
                SELECT MAX(id)
                FROM cached_picks
                GROUP BY source
            )
            ORDER BY fetched_at DESC
            """
        ).fetchall()

    return {
        row["source"]: json.loads(row["data_json"])
        for row in rows
    }


def get_latest_bracket(conn, year=None):
    """Get the most recent cached bracket data, optionally filtered by year."""
    query = "SELECT data_json FROM cached_bracket"
    params = []
    if year is not None:
        query += " WHERE year = ?"
        params.append(year)
    query += " ORDER BY fetched_at DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    if row:
        return json.loads(row["data_json"])
    return None


def get_latest_bracket_record(conn, year=None):
    """Get the most recent cached bracket row with parsed JSON."""
    query = "SELECT year, data_json, fetched_at FROM cached_bracket"
    params = []
    if year is not None:
        query += " WHERE year = ?"
        params.append(year)
    query += " ORDER BY fetched_at DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    if not row:
        return None
    return {
        "year": row["year"],
        "data": json.loads(row["data_json"]),
        "fetched_at": row["fetched_at"],
    }


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


def get_team_list(conn, source=None, year=None):
    """Get list of team names from cached ratings (for autocomplete)."""
    ratings = get_latest_ratings(conn, source=source, year=year)
    if ratings:
        return sorted(ratings.keys())
    return []


def _table_has_column(conn, table_name, column_name):
    """Check whether a SQLite table already has a column."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _ensure_column(conn, table_name, column_name, column_def):
    """Add a column if it does not already exist."""
    if _table_has_column(conn, table_name, column_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
