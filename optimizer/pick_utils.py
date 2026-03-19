"""Utilities for working with public pick percentage data."""

import config


def default_pick_pct(seed: int, round_reaching: int) -> float:
    """Default public pick percentage when no source data is available."""
    defaults = {
        1: {2: 0.97, 3: 0.85, 4: 0.65, 5: 0.40, 6: 0.25, 7: 0.15},
        2: {2: 0.93, 3: 0.72, 4: 0.45, 5: 0.25, 6: 0.13, 7: 0.07},
        3: {2: 0.85, 3: 0.55, 4: 0.28, 5: 0.12, 6: 0.05, 7: 0.02},
        4: {2: 0.80, 3: 0.45, 4: 0.20, 5: 0.08, 6: 0.03, 7: 0.01},
        5: {2: 0.65, 3: 0.30, 4: 0.12, 5: 0.04, 6: 0.01, 7: 0.005},
        6: {2: 0.62, 3: 0.28, 4: 0.10, 5: 0.03, 6: 0.01, 7: 0.004},
        7: {2: 0.58, 3: 0.25, 4: 0.08, 5: 0.03, 6: 0.01, 7: 0.003},
        8: {2: 0.48, 3: 0.18, 4: 0.06, 5: 0.02, 6: 0.005, 7: 0.002},
        9: {2: 0.42, 3: 0.15, 4: 0.05, 5: 0.015, 6: 0.004, 7: 0.001},
        10: {2: 0.38, 3: 0.13, 4: 0.04, 5: 0.01, 6: 0.003, 7: 0.001},
        11: {2: 0.35, 3: 0.12, 4: 0.04, 5: 0.01, 6: 0.003, 7: 0.001},
        12: {2: 0.32, 3: 0.10, 4: 0.03, 5: 0.008, 6: 0.002, 7: 0.0005},
        13: {2: 0.18, 3: 0.04, 4: 0.01, 5: 0.002, 6: 0.0005, 7: 0.0001},
        14: {2: 0.12, 3: 0.02, 4: 0.005, 5: 0.001, 6: 0.0002, 7: 0.00005},
        15: {2: 0.05, 3: 0.01, 4: 0.002, 5: 0.0004, 6: 0.0001, 7: 0.00002},
        16: {2: 0.02, 3: 0.003, 4: 0.0005, 5: 0.0001, 6: 0.00002, 7: 0.000005},
    }

    seed_defaults = defaults.get(seed, defaults[8])
    return seed_defaults.get(round_reaching, 0.01)


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
        # Detect old convention (keys 1-6) vs new convention (keys 2-7).
        # Old convention: has key 1, max key <= 6, no key 7.
        # New convention: has key 7 (or keys only in 2-7 range).
        # Mixed/ambiguous: has key 1 AND key 7 — drop key 1 only.
        if 1 in int_rounds:
            has_new_keys = 7 in int_rounds or max(int_rounds.keys()) > 6
            if not has_new_keys:
                # Pure old convention: shift all keys forward by 1
                int_rounds = {round_num + 1: v for round_num, v in int_rounds.items()}
            else:
                # Already new convention with a stray key 1: just drop it.
                # Key 1 in new convention would mean "in tournament" (always ~1.0)
                # which is not useful for pick percentages.
                del int_rounds[1]
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


def get_round_pick_pct(
    pick_pcts: dict[str, dict[int, float]],
    team_name: str,
    seed: int,
    round_reaching: int,
) -> float:
    """Get a team's public pick rate for reaching a given round."""
    return get_pick_pct(
        pick_pcts,
        team_name,
        round_reaching,
        default_pick_pct(seed, round_reaching),
    )


def get_matchup_pick_prob(
    pick_pcts: dict[str, dict[int, float]],
    team_a_name: str,
    team_a_seed: int,
    team_b_name: str,
    team_b_seed: int,
    round_reaching: int,
) -> float:
    """Estimate public pick probability for team A in a specific matchup."""
    pct_a = get_round_pick_pct(pick_pcts, team_a_name, team_a_seed, round_reaching)
    pct_b = get_round_pick_pct(pick_pcts, team_b_name, team_b_seed, round_reaching)
    total = pct_a + pct_b
    if total <= 0:
        return 0.5
    return pct_a / total


def build_consensus_pick_pcts(
    picks_by_source: dict[str, dict[str, dict[int, float]]] | None,
    source_weights: dict[str, float] | None = None,
    allowed_teams: set[str] | None = None,
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
        source: filter_pick_pcts_to_teams(picks, allowed_teams)
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


def filter_pick_pcts_to_teams(
    pick_pcts: dict[str, dict[int, float]] | None,
    allowed_teams: set[str] | None,
) -> dict[str, dict[int, float]]:
    """Keep only pick rows that belong to the current bracket field."""
    normalized = normalize_pick_pcts(pick_pcts)
    if not normalized or not allowed_teams:
        return normalized

    return {
        team: rounds
        for team, rounds in normalized.items()
        if team in allowed_teams
    }


def extract_bracket_team_names(bracket_data: dict | None) -> set[str]:
    """Extract the 64 team names from a stored bracket JSON payload."""
    if not bracket_data:
        return set()

    team_names = set()
    for region in bracket_data.get("regions") or []:
        for team_name in (region.get("teams") or {}).values():
            if team_name:
                team_names.add(str(team_name))
    return team_names


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
