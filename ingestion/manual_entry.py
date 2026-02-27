"""Manual data entry and CSV loading fallbacks.

Used when scraping is unavailable or the user prefers manual input.
"""

import csv
import os

import pandas as pd


def load_ratings_from_csv(filepath: str) -> dict[str, dict]:
    """Load team ratings from a user-prepared CSV.

    Expected columns: team, rating [, adj_offense, adj_defense, conference]
    Rating should be 0-1 scale (like Barthag).
    """
    df = pd.read_csv(filepath)
    ratings = {}

    for _, row in df.iterrows():
        name = str(row.iloc[0]).strip()
        ratings[name] = {
            "rating": float(row.iloc[1]) if len(row) > 1 else 0.5,
            "adj_offense": float(row.iloc[2]) if len(row) > 2 else 100.0,
            "adj_defense": float(row.iloc[3]) if len(row) > 3 else 100.0,
            "conference": str(row.iloc[4]).strip() if len(row) > 4 else "",
        }

    print(f"Loaded ratings for {len(ratings)} teams from {filepath}")
    return ratings


def load_pick_pcts_from_csv(filepath: str) -> dict[str, dict[int, float]]:
    """Load public pick percentages from a user-prepared CSV.

    Expected columns: team, r1_pct, r2_pct, r3_pct, r4_pct, r5_pct, r6_pct
    Percentages should be 0-100 (will be converted to 0-1).
    If only champion % is provided, other rounds are estimated from seed.
    """
    df = pd.read_csv(filepath)
    pick_pcts = {}

    for _, row in df.iterrows():
        name = str(row.iloc[0]).strip()
        pcts = {}
        for r in range(1, 7):
            col_idx = r  # columns 1-6 map to rounds 1-6
            if col_idx < len(row):
                try:
                    val = float(row.iloc[col_idx])
                    # Convert from 0-100 to 0-1 if needed
                    pcts[r] = val / 100.0 if val > 1.0 else val
                except (ValueError, TypeError):
                    pass

        if pcts:
            pick_pcts[name] = pcts

    print(f"Loaded pick percentages for {len(pick_pcts)} teams from {filepath}")
    return pick_pcts


def estimate_round_picks_from_champion(champ_pct: float, seed: int) -> dict[int, float]:
    """Estimate per-round pick percentages when only champion % is known.

    Uses historical averages of how public pick percentages decay by round and seed.
    """
    # Rough multipliers: if X% pick team as champion,
    # what % pick them in each prior round?
    # These are approximate historical patterns.
    seed_multipliers = {
        1: {1: 0.98, 2: 0.90, 3: 0.75, 4: 0.55, 5: 0.40},
        2: {1: 0.95, 2: 0.82, 3: 0.60, 4: 0.40, 5: 0.28},
        3: {1: 0.90, 2: 0.70, 3: 0.45, 4: 0.25, 5: 0.15},
        4: {1: 0.85, 2: 0.60, 3: 0.35, 4: 0.18, 5: 0.10},
    }

    # For seeds 5+, use generic low multipliers
    default_mult = {1: 0.75, 2: 0.45, 3: 0.20, 4: 0.08, 5: 0.04}

    multipliers = seed_multipliers.get(seed, default_mult)

    pcts = {6: champ_pct}
    for r in range(5, 0, -1):
        # Each earlier round should have higher pick %
        # The champion pick % is the minimum; scale up
        base = multipliers.get(r, 0.5)
        pcts[r] = min(0.99, max(champ_pct, base))

    return pcts
