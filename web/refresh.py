"""Data refresh - fetch latest ratings, picks, and bracket data."""

import config
import json
import os
import sys
import traceback

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from optimizer.pick_utils import build_consensus_pick_pcts, extract_bracket_team_names, filter_pick_pcts_to_teams
from optimizer.rating_utils import build_consensus_ratings
from web.database import get_latest_bracket_record, get_latest_ratings, get_pick_sources


def _get_reference_ratings(conn, year=2026):
    """Build a broad ratings table for name resolution across ingest steps."""
    ratings_by_source = {}
    for source in config.RATING_SOURCE_WEIGHTS:
        ratings = get_latest_ratings(conn, source=source, year=year)
        if not ratings and year is not None:
            ratings = get_latest_ratings(conn, source=source)
        if ratings:
            ratings_by_source[source] = ratings

    consensus = build_consensus_ratings(ratings_by_source)
    if consensus:
        return consensus
    return get_latest_ratings(conn) or {}


def refresh_ratings(conn, source="torvik", year=2026):
    """Fetch latest team ratings and store in DB."""
    try:
        from ingestion.ratings_sources import fetch_ratings_from_source

        ratings = fetch_ratings_from_source(source, year=year, save=False)

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


def refresh_picks(conn, source="yahoo", year=2026, game_key=None, challenge_id=None):
    """Fetch latest public pick percentages and store in DB."""
    try:
        ratings = _get_reference_ratings(conn, year=year)
        bracket_record = get_latest_bracket_record(conn, year=year)
        bracket_teams = extract_bracket_team_names(bracket_record["data"]) if bracket_record else set()
        if source == "espn":
            from ingestion.pick_popularity import fetch_espn_picks
            pick_pcts = fetch_espn_picks(
                year=year,
                ratings=ratings,
                challenge_id=challenge_id,
                bracket_teams=bracket_teams,
            )
        elif source == "yahoo":
            from ingestion.pick_popularity import fetch_yahoo_picks
            pick_pcts = fetch_yahoo_picks(year=year, game_key=game_key, ratings=ratings)
        elif source == "ncaa":
            from ingestion.pick_popularity import fetch_ncaa_picks
            pick_pcts = fetch_ncaa_picks(ratings=ratings)
        elif source == "cbs":
            from ingestion.pick_popularity import fetch_cbs_picks
            pick_pcts = fetch_cbs_picks(ratings=ratings)
        else:
            raise ValueError(f"Unknown picks source: {source}")

        pick_pcts = filter_pick_pcts_to_teams(pick_pcts, bracket_teams)
        metadata = {"source": source, "year": year, "count": len(pick_pcts), "configured": True}
        if not pick_pcts:
            conn.execute(
                "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
                (f"picks:{source}", "No pick data available from public source")
            )
            conn.commit()
            return metadata

        # Convert int keys to str for JSON serialization
        serializable = {}
        for team, rounds in pick_pcts.items():
            serializable[team] = {str(r): v for r, v in rounds.items()}

        pick_columns = {row["name"] for row in conn.execute("PRAGMA table_info(cached_picks)").fetchall()}
        if "year" in pick_columns:
            conn.execute(
                "INSERT INTO cached_picks (source, year, data_json) VALUES (?, ?, ?)",
                (source, year, json.dumps(serializable))
            )
        else:
            conn.execute(
                "INSERT INTO cached_picks (source, data_json) VALUES (?, ?)",
                (source, json.dumps(serializable))
            )
        conn.execute(
            "INSERT INTO refresh_log (source, status, message) VALUES (?, 'success', ?)",
            (f"picks:{source}", f"Loaded picks for {len(pick_pcts)} teams")
        )
        conn.commit()
        return metadata

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

                ratings = _get_reference_ratings(conn, year=year)
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


def refresh_all(conn, year=2026, bracket_game_key=None, ratings_sources=None, espn_challenge_id=None):
    """Refresh all available data sources."""
    results = {}

    rating_sources = ratings_sources or config.DEFAULT_REFRESH_RATING_SOURCES
    rating_statuses = []
    rating_errors = []
    for source in rating_sources:
        try:
            n = refresh_ratings(conn, source, year)
            rating_statuses.append(f"{source}={n} teams")
        except Exception as e:
            rating_errors.append(f"{source}={e}")

    if rating_statuses:
        rating_summary = ", ".join(rating_statuses)
        if rating_errors:
            rating_summary += f"; errors: {'; '.join(rating_errors)}"
        results["ratings"] = f"OK ({rating_summary})"
    elif rating_errors:
        results["ratings"] = f"Error: {'; '.join(rating_errors)}"
    else:
        results["ratings"] = "No ratings source requested"

    bracket_year = year
    try:
        metadata = refresh_bracket(conn, year=year, source="yahoo", game_key=bracket_game_key)
        bracket_year = int(metadata.get("year", year))
        label = f"OK ({metadata.get('source', 'yahoo')}"
        if metadata.get("game_key") is not None:
            label += f" gameKey={metadata['game_key']}"
        label += ")"
        results["bracket"] = label
    except Exception as e:
        results["bracket"] = f"Error: {e}"

    pick_statuses = []
    pick_errors = []
    for source in ("yahoo", "espn", "ncaa", "cbs"):
        try:
            metadata = refresh_picks(
                conn,
                source=source,
                year=bracket_year,
                game_key=bracket_game_key,
                challenge_id=espn_challenge_id if source == "espn" else None,
            )
            if metadata["count"] > 0:
                pick_statuses.append(f"{source}={metadata['count']}")
            elif source in ("yahoo", "espn"):
                pick_statuses.append(f"{source}=empty")
        except Exception as e:
            pick_errors.append(f"{source}={e}")

    bracket_record = get_latest_bracket_record(conn, year=bracket_year)
    bracket_teams = extract_bracket_team_names(bracket_record["data"]) if bracket_record else set()
    consensus_picks = build_consensus_pick_pcts(
        get_pick_sources(conn, year=bracket_year),
        allowed_teams=bracket_teams,
    )
    if consensus_picks:
        summary_parts = pick_statuses + pick_errors
        source_summary = ", ".join(summary_parts) if summary_parts else "sources refreshed"
        results["picks"] = f"OK ({len(consensus_picks)} teams consensus; {source_summary})"
    elif pick_errors:
        results["picks"] = f"Error: {'; '.join(pick_errors)}"
    else:
        checked = ", ".join(pick_statuses) if pick_statuses else "no sources configured"
        results["picks"] = f"No data available ({checked})"

    return results
