"""Neil Paine forecast ingestion.

The attached CSV carries a projected rating plus round-by-round odds. We use
the rating column as an additional power-rating source for the consensus blend.
"""

from __future__ import annotations

import math
import os
import shutil

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def fetch_neil_paine_ratings(
    year: int = 2026,
    save: bool = True,
    file: str | None = None,
) -> pd.DataFrame:
    """Load Neil Paine's forecast CSV from a local path."""
    source_path = _resolve_csv_path(year, file)
    df = _read_neil_paine_csv(source_path)

    target_path = os.path.join(DATA_DIR, f"neil_paine_{year}.csv")
    if save and os.path.abspath(source_path) != os.path.abspath(target_path):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copyfile(source_path, target_path)

    return df


def parse_neil_paine_ratings(df: pd.DataFrame) -> dict[str, dict]:
    """Parse Neil Paine's rating export into the shared 0-1 ratings format."""
    columns = {_normalize_col(col): col for col in df.columns}
    team_col = columns.get("team")
    rtg_col = columns.get("rtg")
    if not team_col or not rtg_col:
        raise ValueError(f"Could not find Team/Rtg columns in Neil Paine CSV. Columns: {list(df.columns)}")

    ratings: dict[str, dict] = {}
    for _, row in df.iterrows():
        name = str(row.get(team_col, "")).strip()
        if not name or name.lower() == "nan":
            continue

        raw_rating = _safe_float(row.get(rtg_col))
        if raw_rating is None:
            continue

        ratings[name] = {
            "rating": _rtg_to_rating(raw_rating),
            "adj_offense": 100.0,
            "adj_defense": 100.0,
            "raw_rating": raw_rating,
        }

    return ratings


def _resolve_csv_path(year: int, file: str | None) -> str:
    """Resolve the best available local CSV path."""
    candidates = []

    if file:
        candidates.append(file)

    env_path = os.environ.get("SEED_MONEY_NEIL_PAINE_CSV_PATH", "").strip()
    if env_path:
        candidates.append(env_path)

    candidates.append(os.path.join(DATA_DIR, f"neil_paine_{year}.csv"))

    for path in candidates:
        if path and os.path.exists(path):
            return path

    raise ValueError(
        "Neil Paine ratings are not configured. Add "
        f"data/raw/neil_paine_{year}.csv or set SEED_MONEY_NEIL_PAINE_CSV_PATH."
    )


def _read_neil_paine_csv(path: str) -> pd.DataFrame:
    """Read the CSV, tolerating the two-row header used in the export."""
    header_attempts = (1, 0)
    last_error: Exception | None = None

    for header_row in header_attempts:
        try:
            df = pd.read_csv(path, header=header_row)
            columns = {_normalize_col(col): col for col in df.columns}
            if "team" in columns and "rtg" in columns:
                return df
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise ValueError(f"Could not parse Neil Paine CSV at {path}")


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


def _rtg_to_rating(rtg: float) -> float:
    """Map Neil Paine's rating scale into a 0-1 log5-friendly strength value."""
    return max(0.001, min(0.999, 1.0 / (1.0 + math.exp(-float(rtg) / 12.0))))
