"""Helpers for using direct round-probability forecasts when available."""

from __future__ import annotations

import json
import os

import config
from optimizer.simulator import simulate_tournament

ALIASES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "team_aliases.json")


def resolve_reach_probs(bracket,
                        ratings: dict[str, dict] | None,
                        n_sims: int = config.DEFAULT_SIMULATIONS,
                        seed: int | None = 42,
                        show_progress: bool = True) -> dict[str, dict[int, float]]:
    """Use direct forecast reach probabilities when present, else simulate."""
    direct = extract_direct_reach_probs_for_bracket(bracket, ratings)
    if direct and _has_complete_coverage(bracket, direct):
        return direct

    simulated = simulate_tournament(bracket, n_sims=n_sims, seed=seed, show_progress=show_progress)
    if not direct:
        return simulated

    for team_name, rounds in direct.items():
        simulated.setdefault(team_name, {})
        for round_num, value in rounds.items():
            simulated[team_name][round_num] = value

    return simulated


def extract_direct_reach_probs_for_bracket(bracket,
                                           ratings: dict[str, dict] | None) -> dict[str, dict[int, float]]:
    """Extract source-provided reach probabilities keyed to bracket team names."""
    if not ratings:
        return {}

    aliases = _load_aliases()
    canonical_ratings: dict[str, dict] = {}
    for name, entry in ratings.items():
        canonical_ratings[aliases.get(name, name)] = entry

    direct: dict[str, dict[int, float]] = {}
    for team in bracket.teams:
        canonical = aliases.get(team.name, team.name)
        entry = canonical_ratings.get(canonical) or ratings.get(team.name)
        if not entry:
            continue

        rounds = _coerce_reach_probs(entry.get("reach_probs"))
        if not rounds:
            continue
        setattr(team, "reach_probs", rounds)
        direct[team.name] = rounds

    return direct


def _has_complete_coverage(bracket, direct: dict[str, dict[int, float]]) -> bool:
    """Check whether every bracket team has all rounds 1-7 from a direct source."""
    team_names = {team.name for team in bracket.teams}
    if set(direct.keys()) != team_names:
        return False
    return all(all(round_num in direct[name] for round_num in range(1, 8)) for name in team_names)


def _coerce_reach_probs(value) -> dict[int, float]:
    """Normalize reach-probability keys to ints and clamp values."""
    if not isinstance(value, dict):
        return {}

    rounds: dict[int, float] = {}
    for round_key, round_value in value.items():
        try:
            round_num = int(round_key)
            prob = float(round_value)
        except (TypeError, ValueError):
            continue
        rounds[round_num] = max(0.0, min(1.0, prob))

    return rounds


def _load_aliases() -> dict[str, str]:
    """Load the shared team alias table."""
    if not os.path.exists(ALIASES_PATH):
        return {}
    with open(ALIASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
