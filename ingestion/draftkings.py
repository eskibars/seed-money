"""DraftKings implied-odds ingestion.

The attached CSV contains market-implied advancement odds by stage. We convert
that profile into a 0-1 team-strength rating so it can participate in the
simulation consensus alongside other sources.
"""

from __future__ import annotations

import math
import os
import shutil

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def fetch_draftkings_ratings(
    year: int = 2026,
    save: bool = True,
    file: str | None = None,
) -> pd.DataFrame:
    """Load DraftKings implied-odds CSV from a local path."""
    source_path = _resolve_csv_path(year, file)
    df = pd.read_csv(source_path)

    target_path = os.path.join(DATA_DIR, f"draftkings_{year}.csv")
    if save and os.path.abspath(source_path) != os.path.abspath(target_path):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copyfile(source_path, target_path)

    return df


def parse_draftkings_ratings(df: pd.DataFrame) -> dict[str, dict]:
    """Parse DraftKings implied odds into the shared 0-1 ratings format."""
    columns = {_normalize_col(col): col for col in df.columns}
    required = {
        "team": columns.get("team"),
        "r1_implied": columns.get("r1_implied"),
        "s16_implied": columns.get("s16_implied"),
        "e8_implied": columns.get("e8_implied"),
        "f4_implied": columns.get("f4_implied"),
        "championship_implied": columns.get("championship_implied"),
    }
    missing = [name for name, col in required.items() if not col]
    if missing:
        raise ValueError(
            "Could not find expected DraftKings columns "
            f"{missing}. Columns: {list(df.columns)}"
        )

    rows: list[tuple[str, dict[str, float], float]] = []
    for _, row in df.iterrows():
        name = str(row.get(required["team"], "")).strip()
        if not name or name.lower() == "nan" or "/" in name:
            # Combined play-in slots like TX/NCST are not team-specific.
            continue

        implied = {
            "r1_implied": _safe_float(row.get(required["r1_implied"])) or 0.0,
            "s16_implied": _safe_float(row.get(required["s16_implied"])) or 0.0,
            "e8_implied": _safe_float(row.get(required["e8_implied"])) or 0.0,
            "f4_implied": _safe_float(row.get(required["f4_implied"])) or 0.0,
            "championship_implied": _safe_float(row.get(required["championship_implied"])) or 0.0,
        }
        rows.append((name, implied, _market_score(implied)))

    if not rows:
        return {}

    min_score = min(score for _, _, score in rows)
    max_score = max(score for _, _, score in rows)

    ratings: dict[str, dict] = {}
    for name, implied, score in rows:
        ratings[name] = {
            "rating": _score_to_rating(score, min_score, max_score),
            "adj_offense": 100.0,
            "adj_defense": 100.0,
            "market_score": score,
            **implied,
        }

    return ratings


def _resolve_csv_path(year: int, file: str | None) -> str:
    """Resolve the best available local CSV path."""
    candidates = []

    if file:
        candidates.append(file)

    env_path = os.environ.get("SEED_MONEY_DRAFTKINGS_CSV_PATH", "").strip()
    if env_path:
        candidates.append(env_path)

    candidates.append(os.path.join(DATA_DIR, f"draftkings_{year}.csv"))

    for path in candidates:
        if path and os.path.exists(path):
            return path

    raise ValueError(
        "DraftKings implied odds are not configured. Add "
        f"data/raw/draftkings_{year}.csv or set SEED_MONEY_DRAFTKINGS_CSV_PATH."
    )


def _market_score(implied: dict[str, float]) -> float:
    """Collapse advancement odds into a single expected-strength score."""
    final_proxy = math.sqrt(
        max(implied.get("f4_implied", 0.0), 0.0)
        * max(implied.get("championship_implied", 0.0), 0.0)
    )
    return (
        implied.get("r1_implied", 0.0)
        + implied.get("s16_implied", 0.0)
        + implied.get("e8_implied", 0.0)
        + implied.get("f4_implied", 0.0)
        + final_proxy
        + implied.get("championship_implied", 0.0)
    )


def _score_to_rating(score: float, min_score: float, max_score: float) -> float:
    """Map relative market strength into the project's 0-1 rating scale."""
    if max_score <= min_score:
        return 0.50
    normalized = (score - min_score) / (max_score - min_score)
    return max(0.001, min(0.999, 0.18 + 0.80 * normalized))


def _normalize_col(value) -> str:
    """Normalize CSV column names for easier matching."""
    return str(value).strip().lower().replace(" ", "")


def _safe_float(value) -> float | None:
    """Convert a numeric-ish cell to float."""
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None
