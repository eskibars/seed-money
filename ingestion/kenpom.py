"""KenPom data ingestion.

Supports either:
1. A local CSV dropped into data/raw/kenpom_{year}.csv
2. A configured local path via SEED_MONEY_KENPOM_CSV_PATH
3. A configured CSV URL via SEED_MONEY_KENPOM_CSV_URL

The parser prefers a Pyth-style 0-1 rating when available and otherwise
derives one from adjusted offense/defense.
"""

from __future__ import annotations

import math
import os
from io import StringIO

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
KENPOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/csv,text/plain,*/*",
}


def fetch_kenpom_ratings(year: int = 2026, save: bool = True) -> pd.DataFrame:
    """Load KenPom ratings from a configured local file or URL."""
    csv_text = _load_kenpom_csv_text(year)

    if save:
        os.makedirs(DATA_DIR, exist_ok=True)
        csv_path = os.path.join(DATA_DIR, f"kenpom_{year}.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_text)

    return pd.read_csv(StringIO(csv_text))


def parse_kenpom_ratings(df: pd.DataFrame) -> dict[str, dict]:
    """Parse KenPom ratings into the shared team-rating format."""
    ratings = {}

    team_col = _find_col(df, ["team", "Team"])
    pyth_col = _find_col(df, ["pyth", "Pyth"])
    adjem_col = _find_col(df, ["adjem", "AdjEM", "Adj Em", "NetRtg", "Net Rating"])
    adjo_col = _find_col(df, ["adjo", "AdjO", "AdjOE", "Adj O", "ORtg", "OffRtg", "Off Rating"])
    adjd_col = _find_col(df, ["adjd", "AdjD", "AdjDE", "Adj D", "DRtg", "DefRtg", "Def Rating"])
    conf_col = _find_col(df, ["conf", "Conf", "conference", "Conference"])

    if not team_col:
        raise ValueError(f"Could not find team column in KenPom CSV. Columns: {list(df.columns)}")

    for _, row in df.iterrows():
        try:
            name = str(row[team_col]).strip()
            if not name:
                continue

            adj_offense = _safe_float(row[adjo_col]) if adjo_col else 100.0
            adj_defense = _safe_float(row[adjd_col]) if adjd_col else 100.0

            if pyth_col:
                rating = _normalize_prob(_safe_float(row[pyth_col]))
            elif adjo_col and adjd_col and adj_offense > 0 and adj_defense > 0:
                rating = _pyth_from_efficiency(adj_offense, adj_defense)
            elif adjem_col:
                rating = _normalize_prob(_adjem_to_rating(_safe_float(row[adjem_col])))
            else:
                continue
        except (TypeError, ValueError):
            continue

        ratings[name] = {
            "rating": rating,
            "adj_offense": adj_offense,
            "adj_defense": adj_defense,
            "conference": str(row[conf_col]).strip() if conf_col else "",
        }

    return ratings


def _load_kenpom_csv_text(year: int) -> str:
    """Load raw CSV text from a configured source."""
    path_candidates = []

    env_path = os.environ.get("SEED_MONEY_KENPOM_CSV_PATH", "").strip()
    if env_path:
        path_candidates.append(_format_year_token(env_path, year))

    path_candidates.append(os.path.join(DATA_DIR, f"kenpom_{year}.csv"))

    for path in path_candidates:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

    env_url = os.environ.get("SEED_MONEY_KENPOM_CSV_URL", "").strip()
    if env_url:
        url = _format_year_token(env_url, year)
        resp = requests.get(url, headers=KENPOM_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text

    raise RuntimeError(
        "KenPom ratings are not configured. Add "
        f"data/raw/kenpom_{year}.csv or set SEED_MONEY_KENPOM_CSV_PATH / "
        "SEED_MONEY_KENPOM_CSV_URL."
    )


def _format_year_token(template: str, year: int) -> str:
    """Expand {year} in a configured path or URL if present."""
    return template.format(year=year) if "{year}" in template else template


def _pyth_from_efficiency(adj_offense: float, adj_defense: float) -> float:
    """Approximate KenPom's pythagorean expectation from efficiencies."""
    exponent = 11.5
    offense_term = max(adj_offense, 1e-6) ** exponent
    defense_term = max(adj_defense, 1e-6) ** exponent
    return _normalize_prob(offense_term / (offense_term + defense_term))


def _adjem_to_rating(adjem: float) -> float:
    """Map efficiency margin to a 0-1 win-probability-style rating."""
    return 1.0 / (1.0 + math.exp(-adjem / 11.0))


def _normalize_prob(value: float) -> float:
    """Clamp a probability-like value into a safe 0-1 range."""
    if value > 1.0 and value <= 100.0:
        value /= 100.0
    return min(0.999, max(0.001, value))


def _safe_float(value) -> float:
    """Convert a CSV cell to float."""
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text.lower() == "nan":
        raise ValueError("empty numeric value")
    return float(text)


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find a column by trying multiple possible names."""
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
        for actual in df.columns:
            if actual.strip().lower() == candidate.lower():
                return actual
    return None
