"""Bart Torvik data ingestion.

Downloads team ratings from barttorvik.com. Free, no auth required.
The CSV contains ~360 teams with columns including:
- Team name, conference
- Barthag (overall power rating 0-1)
- Adjusted offensive/defensive efficiency (adjoe, adjde)
"""

import os
import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def fetch_torvik_ratings(year: int = 2026, save: bool = True) -> pd.DataFrame:
    """Download Bart Torvik team ratings for a given season.

    Args:
        year: Season year (e.g. 2026 for the 2025-26 season)
        save: Whether to save the CSV to data/raw/

    Returns:
        DataFrame with team ratings
    """
    url = f"https://barttorvik.com/{year}_team_results.csv"
    print(f"Fetching Torvik ratings from {url}...")

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # Save raw CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    csv_path = os.path.join(DATA_DIR, f"torvik_{year}.csv")
    if save:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"Saved to {csv_path}")

    # Parse into DataFrame
    from io import StringIO
    df = pd.read_csv(StringIO(resp.text))

    return df


def parse_torvik_ratings(df: pd.DataFrame) -> dict[str, dict]:
    """Parse Torvik DataFrame into a dict of team ratings.

    Returns:
        {team_name: {"rating": barthag, "adj_offense": adjoe, "adj_defense": adjde, ...}}
    """
    ratings = {}

    # Column names vary slightly by year; find them flexibly
    team_col = _find_col(df, ["team", "Team"])
    barthag_col = _find_col(df, ["barthag", "Barthag", "BARTHAG"])
    adjoe_col = _find_col(df, ["adjoe", "AdjOE", "adj_oe", "Adj OE"])
    adjde_col = _find_col(df, ["adjde", "AdjDE", "adj_de", "Adj DE"])
    conf_col = _find_col(df, ["conf", "Conf", "conference", "Conference"])

    if not team_col or not barthag_col:
        # Fall back to positional if headers are missing
        print("Warning: Could not find expected columns. Using positional fallback.")
        print(f"Available columns: {list(df.columns)}")
        # Torvik CSV typically: rank, team, conf, record, adjoe, adjde, barthag, ...
        for _, row in df.iterrows():
            try:
                name = str(row.iloc[1]).strip()
                ratings[name] = {
                    "rating": float(row.iloc[6]) if len(row) > 6 else 0.5,
                    "adj_offense": float(row.iloc[4]) if len(row) > 4 else 100.0,
                    "adj_defense": float(row.iloc[5]) if len(row) > 5 else 100.0,
                    "conference": str(row.iloc[2]).strip() if len(row) > 2 else "",
                }
            except (ValueError, IndexError):
                continue
        return ratings

    for _, row in df.iterrows():
        name = str(row[team_col]).strip()
        try:
            ratings[name] = {
                "rating": float(row[barthag_col]),
                "adj_offense": float(row[adjoe_col]) if adjoe_col else 100.0,
                "adj_defense": float(row[adjde_col]) if adjde_col else 100.0,
                "conference": str(row[conf_col]).strip() if conf_col else "",
            }
        except (ValueError, TypeError):
            continue

    return ratings


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find a column by trying multiple possible names."""
    for c in candidates:
        if c in df.columns:
            return c
        # Case-insensitive fallback
        for actual in df.columns:
            if actual.strip().lower() == c.lower():
                return actual
    return None
