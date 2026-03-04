"""Data refresh — fetch latest ratings, picks, and bracket data."""

import json
import os
import sys
import traceback

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.database import get_db


def refresh_ratings(conn, source="torvik", year=2026):
    """Fetch latest team ratings and store in DB."""
    try:
        if source == "torvik":
            from ingestion.torvik import fetch_torvik_ratings, parse_torvik_ratings
            df = fetch_torvik_ratings(year=year, save=False)
            ratings = parse_torvik_ratings(df)
        elif source == "espn":
            from ingestion.espn_bpi import fetch_espn_bpi, bpi_to_rating
            raw = fetch_espn_bpi()
            ratings = {}
            for name, data in raw.items():
                ratings[name] = {
                    "rating": bpi_to_rating(data["bpi"]),
                    "adj_offense": 100.0,
                    "adj_defense": 100.0,
                }
        else:
            raise ValueError(f"Unknown ratings source: {source}")

        conn.execute(
            "INSERT INTO cached_ratings (source, year, data_json) VALUES (?, ?, ?)",
            (source, year, json.dumps(ratings))
        )
        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
            (f"ratings:{source}", f"Loaded {len(ratings)} teams")
        )
        conn.commit()
        return len(ratings)

    except Exception as e:
        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'error', ?)",
            (f"ratings:{source}", traceback.format_exc())
        )
        conn.commit()
        raise


def refresh_picks(conn, source="espn"):
    """Fetch latest public pick percentages and store in DB."""
    try:
        if source == "espn":
            from ingestion.pick_popularity import fetch_espn_picks
            pick_pcts = fetch_espn_picks()
        elif source == "yahoo":
            from ingestion.pick_popularity import fetch_yahoo_picks
            pick_pcts = fetch_yahoo_picks()
        else:
            raise ValueError(f"Unknown picks source: {source}")

        if not pick_pcts:
            conn.execute(
                "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
                (f"picks:{source}", "No pick data available (tournament not started)")
            )
            conn.commit()
            return 0

        # Convert int keys to str for JSON serialization
        serializable = {}
        for team, rounds in pick_pcts.items():
            serializable[team] = {str(r): v for r, v in rounds.items()}

        conn.execute(
            "INSERT INTO cached_picks (source, data_json) VALUES (?, ?)",
            (source, json.dumps(serializable))
        )
        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
            (f"picks:{source}", f"Loaded picks for {len(pick_pcts)} teams")
        )
        conn.commit()
        return len(pick_pcts)

    except Exception as e:
        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'error', ?)",
            (f"picks:{source}", traceback.format_exc())
        )
        conn.commit()
        raise


def refresh_bracket(conn, bracket_json, year=2026):
    """Store bracket data in DB (must be provided, not fetched)."""
    conn.execute(
        "INSERT INTO cached_bracket (year, data_json) VALUES (?, ?)",
        (year, json.dumps(bracket_json))
    )
    conn.execute(
        "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
        ("bracket", f"Loaded bracket for {year}")
    )
    conn.commit()


def refresh_all(conn, year=2026):
    """Refresh all available data sources."""
    results = {}

    try:
        n = refresh_ratings(conn, "torvik", year)
        results["ratings"] = f"OK ({n} teams)"
    except Exception as e:
        results["ratings"] = f"Error: {e}"

    try:
        n = refresh_picks(conn, "espn")
        results["picks"] = f"OK ({n} teams)" if n else "No data available"
    except Exception as e:
        results["picks"] = f"Error: {e}"

    return results
