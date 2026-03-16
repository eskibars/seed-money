"""Utilities for working with public pick percentage data."""

import config


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


def build_consensus_pick_pcts(
    picks_by_source: dict[str, dict[str, dict[int, float]]] | None,
    source_weights: dict[str, float] | None = None,
) -> dict[str, dict[int, float]]:
    """Blend multiple pick sources into one consensus matrix.

    The blend is coverage-aware: a source only contributes where it has a value
    for a specific team/round, which makes partial backfills safe to mix with
    full-table sources.
    """
    if not picks_by_source:
        return {}

    weights = dict(config.PICK_SOURCE_WEIGHTS)
    if source_weights:
        weights.update(source_weights)

    normalized_sources = {
        source: normalize_pick_pcts(picks)
        for source, picks in picks_by_source.items()
        if picks
    }
    if not normalized_sources:
        return {}

    all_teams = set()
    for picks in normalized_sources.values():
        all_teams.update(picks.keys())

    consensus: dict[str, dict[int, float]] = {}
    for team in all_teams:
        rounds: dict[int, float] = {}
        round_keys = {
            round_num
            for picks in normalized_sources.values()
            for round_num in picks.get(team, {})
        }
        for round_num in round_keys:
            total_weight = 0.0
            weighted_sum = 0.0
            for source, picks in normalized_sources.items():
                value = picks.get(team, {}).get(round_num)
                if value is None:
                    continue
                weight = weights.get(source, 0.10)
                total_weight += weight
                weighted_sum += weight * value

            if total_weight > 0:
                rounds[round_num] = min(1.0, max(0.0, weighted_sum / total_weight))

        if rounds:
            consensus[team] = rounds

    return consensus


def merge_pick_pcts(
    pick_dicts: list[dict[str, dict[int, float]]],
) -> dict[str, dict[int, float]]:
    """Merge pick dicts by averaging overlapping team/round entries."""
    merged: dict[str, dict[int, list[float]]] = {}

    for picks in pick_dicts:
        normalized = normalize_pick_pcts(picks)
        for team, rounds in normalized.items():
            team_entry = merged.setdefault(team, {})
            for round_num, value in rounds.items():
                team_entry.setdefault(round_num, []).append(value)

    return {
        team: {
            round_num: sum(values) / len(values)
            for round_num, values in rounds.items()
        }
        for team, rounds in merged.items()
    }
