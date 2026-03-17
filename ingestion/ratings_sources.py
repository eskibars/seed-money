"""Shared registry/helpers for team rating providers."""

from __future__ import annotations

from ingestion.manual_entry import load_ratings_from_csv


def fetch_ratings_from_source(source: str,
                              year: int = 2026,
                              save: bool = True,
                              file: str | None = None) -> dict[str, dict]:
    """Fetch or load team ratings from a named source."""
    source = source.strip().lower()

    if source == "torvik":
        from ingestion.torvik import fetch_torvik_ratings, parse_torvik_ratings

        df = fetch_torvik_ratings(year=year, save=save)
        return parse_torvik_ratings(df)

    if source == "kenpom":
        from ingestion.kenpom import fetch_kenpom_ratings, parse_kenpom_ratings

        df = fetch_kenpom_ratings(year=year, save=save)
        return parse_kenpom_ratings(df)

    if source == "espn":
        from ingestion.espn_bpi import fetch_espn_bpi, bpi_to_rating

        raw = fetch_espn_bpi()
        ratings = {}
        for name, data in raw.items():
            ratings[name] = {
                "rating": bpi_to_rating(data["bpi"]),
                "adj_offense": 100.0,
                "adj_defense": 100.0,
            }
        return ratings

    if source == "manual":
        if not file:
            raise ValueError("Manual ratings source requires a CSV file path.")
        return load_ratings_from_csv(file)

    raise ValueError(f"Unknown ratings source: {source}")
