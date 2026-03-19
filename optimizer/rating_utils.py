"""Helpers for blending multiple team-rating sources."""

from __future__ import annotations

import json
import os

import config

ALIASES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "team_aliases.json")


def build_consensus_ratings(
    ratings_by_source: dict[str, dict[str, dict]] | None,
    source_weights: dict[str, float] | None = None,
) -> dict[str, dict]:
    """Blend multiple ratings sources into one consensus ratings table."""
    if not ratings_by_source:
        return {}

    weights = dict(config.RATING_SOURCE_WEIGHTS)
    if source_weights:
        weights.update(source_weights)

    normalized_sources = {
        source: _canonicalize_ratings(ratings)
        for source, ratings in ratings_by_source.items()
        if ratings
    }
    if not normalized_sources:
        return {}

    teams = set()
    for ratings in normalized_sources.values():
        teams.update(ratings.keys())

    consensus: dict[str, dict] = {}
    for team in teams:
        rating_total = 0.0
        rating_weight = 0.0
        offense_total = 0.0
        offense_weight = 0.0
        defense_total = 0.0
        defense_weight = 0.0
        reach_totals = {round_num: 0.0 for round_num in range(1, 8)}
        reach_weights = {round_num: 0.0 for round_num in range(1, 8)}

        for source, ratings in normalized_sources.items():
            entry = ratings.get(team)
            if not entry:
                continue

            weight = float(weights.get(source, 0.10))

            rating = _safe_float(entry.get("rating"))
            if rating is not None:
                rating_total += weight * _clamp_prob(rating)
                rating_weight += weight

            adj_offense = _safe_float(entry.get("adj_offense"))
            if adj_offense is not None:
                offense_total += weight * adj_offense
                offense_weight += weight

            adj_defense = _safe_float(entry.get("adj_defense"))
            if adj_defense is not None:
                defense_total += weight * adj_defense
                defense_weight += weight

            for round_num, prob in _coerce_reach_probs(entry.get("reach_probs")).items():
                reach_totals[round_num] += weight * _clamp_fraction(prob)
                reach_weights[round_num] += weight

        if rating_weight <= 0:
            continue

        consensus_entry = {
            "rating": rating_total / rating_weight,
            "adj_offense": offense_total / offense_weight if offense_weight > 0 else 100.0,
            "adj_defense": defense_total / defense_weight if defense_weight > 0 else 100.0,
        }
        reach_probs = {
            round_num: reach_totals[round_num] / reach_weights[round_num]
            for round_num in range(1, 8)
            if reach_weights[round_num] > 0
        }
        if reach_probs:
            consensus_entry["reach_probs"] = reach_probs

        consensus[team] = consensus_entry

    return consensus


def _canonicalize_ratings(ratings: dict[str, dict]) -> dict[str, dict]:
    """Normalize aliases so source rows can be merged on the same team."""
    aliases = _load_aliases()
    canonicalized: dict[str, list[dict]] = {}

    for name, entry in (ratings or {}).items():
        canonical = aliases.get(name, name)
        canonicalized.setdefault(canonical, []).append(entry or {})

    merged: dict[str, dict] = {}
    for canonical, entries in canonicalized.items():
        rating_values = [_clamp_prob(v) for v in (_safe_float(e.get("rating")) for e in entries) if v is not None]
        offense_values = [v for v in (_safe_float(e.get("adj_offense")) for e in entries) if v is not None]
        defense_values = [v for v in (_safe_float(e.get("adj_defense")) for e in entries) if v is not None]
        reach_values = {round_num: [] for round_num in range(1, 8)}
        for entry in entries:
            for round_num, prob in _coerce_reach_probs(entry.get("reach_probs")).items():
                reach_values[round_num].append(_clamp_fraction(prob))

        if not rating_values:
            continue

        merged_entry = {
            "rating": sum(rating_values) / len(rating_values),
            "adj_offense": sum(offense_values) / len(offense_values) if offense_values else 100.0,
            "adj_defense": sum(defense_values) / len(defense_values) if defense_values else 100.0,
        }
        reach_probs = {
            round_num: sum(values) / len(values)
            for round_num, values in reach_values.items()
            if values
        }
        if reach_probs:
            merged_entry["reach_probs"] = reach_probs

        merged[canonical] = merged_entry

    return merged


def _load_aliases() -> dict[str, str]:
    """Load the shared team alias table."""
    if not os.path.exists(ALIASES_PATH):
        return {}
    with open(ALIASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_float(value) -> float | None:
    """Convert a numeric-ish value to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_prob(value: float) -> float:
    """Clamp a probability-like rating to a safe range."""
    return max(0.001, min(0.999, float(value)))


def _clamp_fraction(value: float) -> float:
    """Clamp a reach probability to a safe 0-1 range without lifting zeroes."""
    return max(0.0, min(1.0, float(value)))


def _coerce_reach_probs(value) -> dict[int, float]:
    """Normalize reach-probability keys to ints."""
    if not isinstance(value, dict):
        return {}

    rounds: dict[int, float] = {}
    for round_key, round_value in value.items():
        try:
            round_num = int(round_key)
            prob = float(round_value)
        except (TypeError, ValueError):
            continue
        rounds[round_num] = prob

    return _repair_reach_prob_sequence(rounds)


def _repair_reach_prob_sequence(rounds: dict[int, float]) -> dict[int, float]:
    """Repair common percent-parsing mistakes and enforce monotonic reach odds."""
    repaired = {round_num: _clamp_fraction(prob) for round_num, prob in rounds.items()}
    previous = None

    for round_num in sorted(repaired):
        value = repaired[round_num]
        if previous is not None and value > previous:
            scaled = value / 100.0
            if scaled <= previous:
                value = scaled
            else:
                value = previous

        repaired[round_num] = _clamp_fraction(value)
        previous = repaired[round_num]

    return repaired
