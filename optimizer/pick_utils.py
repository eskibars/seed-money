"""Utilities for working with public pick percentage data."""


def normalize_pick_pcts(pick_pcts: dict[str, dict[int, float]] | None) -> dict[str, dict[int, float]]:
    """Normalize pick percentages to the "reach round N" convention.

    The optimizer expects keys 2-7:
    2 = win first game, 7 = win championship.

    Older cached/manual data may still use keys 1-6. When that shape is
    detected, shift the rounds forward by one.
    """
    if not pick_pcts:
        return {}

    normalized: dict[str, dict[int, float]] = {}
    for team, rounds in pick_pcts.items():
        int_rounds = {int(r): v for r, v in rounds.items()}
        if 1 in int_rounds:
            int_rounds = {round_num + 1: v for round_num, v in int_rounds.items()}
        normalized[team] = int_rounds

    return normalized


def get_pick_pct(pick_pcts: dict[str, dict[int, float]],
                 team_name: str,
                 round_reaching: int,
                 default: float) -> float:
    """Look up a team's public pick rate with compatibility for legacy data."""
    rounds = pick_pcts.get(team_name, {})
    if round_reaching in rounds:
        return rounds[round_reaching]

    if 1 in rounds and (round_reaching - 1) in rounds:
        return rounds[round_reaching - 1]

    return default
