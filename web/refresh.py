"""Data refresh - fetch latest ratings, picks, and bracket data."""

import json
import os
import sys
import traceback

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.database import get_latest_ratings


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


def refresh_bracket(conn,
                    bracket_json=None,
                    year=2026,
                    source="yahoo",
                    game_key=None):
    """Fetch or store bracket data in DB.

    Args:
        conn: SQLite connection
        bracket_json: Parsed optimizer-style bracket JSON. If omitted, fetch
            from the requested public source.
        year: Tournament year
        source: Public source to fetch from when ``bracket_json`` is omitted
        game_key: Optional Yahoo override for debugging or manual pinning

    Returns:
        Metadata dict describing the loaded bracket.
    """
    try:
        metadata = {"year": year}

        if bracket_json is None:
            if source == "yahoo":
                from ingestion.bracket_fetcher import fetch_yahoo_bracket

                ratings = get_latest_ratings(conn) or {}
                bracket_json, fetched = fetch_yahoo_bracket(
                    year=year,
                    game_key=game_key,
                    ratings=ratings,
                    save=True,
                )
                metadata.update(fetched)
                year = int(fetched.get("season", year))
            else:
                raise ValueError(f"Unknown bracket source: {source}")
        else:
            metadata["source"] = source

        conn.execute(
            "INSERT INTO cached_bracket (year, data_json) VALUES (?, ?)",
            (year, json.dumps(bracket_json))
        )

        source_label = metadata.get("source", "bracket")
        message = f"Loaded bracket for {year}"
        if metadata.get("game_key") is not None:
            message += f" from {source_label} gameKey={metadata['game_key']}"

        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
            ("bracket", message)
        )
        conn.commit()
        metadata["year"] = year
        return metadata

    except Exception:
        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'error', ?)",
            ("bracket", traceback.format_exc())
        )
        conn.commit()
        raise


def refresh_all(conn, year=2026, bracket_game_key=None):
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

    try:
        metadata = refresh_bracket(conn, year=year, source="yahoo", game_key=bracket_game_key)
        label = f"OK ({metadata.get('source', 'yahoo')}"
        if metadata.get("game_key") is not None:
            label += f" gameKey={metadata['game_key']}"
        label += ")"
        results["bracket"] = label
    except Exception as e:
        results["bracket"] = f"Error: {e}"

    return results
