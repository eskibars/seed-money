"""Shared registry/helpers for team rating providers."""

from __future__ import annotations

import config
from ingestion.manual_entry import load_ratings_from_csv
from optimizer.rating_utils import build_consensus_ratings


def fetch_ratings_from_source(source: str,
                              year: int = 2026,
                              save: bool = True,
                              file: str | None = None) -> dict[str, dict]:
    """Fetch or load team ratings from a named source."""
    source = source.strip().lower()

    if source == "consensus":
        ratings_by_source = {}
        errors = []
        for component in config.RATING_SOURCE_WEIGHTS:
            try:
                ratings = fetch_ratings_from_source(component, year=year, save=save, file=None)
            except Exception as exc:
                errors.append(f"{component}={exc}")
                continue
            if ratings:
                ratings_by_source[component] = ratings

        consensus = build_consensus_ratings(ratings_by_source)
        if consensus:
            return consensus

        details = f" Errors: {'; '.join(errors)}" if errors else ""
        raise ValueError(f"Could not build consensus ratings.{details}")

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

    if source == "paine":
        from ingestion.neil_paine import fetch_neil_paine_ratings, parse_neil_paine_ratings

        df = fetch_neil_paine_ratings(year=year, save=save, file=file)
        return parse_neil_paine_ratings(df)

    if source == "draftkings":
        from ingestion.draftkings import fetch_draftkings_ratings, parse_draftkings_ratings

        df = fetch_draftkings_ratings(year=year, save=save, file=file)
        return parse_draftkings_ratings(df)

    if source == "manual":
        if not file:
            raise ValueError("Manual ratings source requires a CSV file path.")
        return load_ratings_from_csv(file)

    raise ValueError(f"Unknown ratings source: {source}")
