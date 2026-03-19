"""Service layer — bridges the web app to existing optimizer modules."""

import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from ingestion.bracket_loader import load_bracket_from_dict
from optimizer.simulator import simulate_tournament
from optimizer.engine import optimize
from optimizer.pick_utils import build_consensus_pick_pcts, extract_bracket_team_names
from optimizer.rating_utils import build_consensus_ratings
from output.html_export import export_bracket_html
from web.database import get_latest_ratings, get_latest_bracket_record, get_pick_sources

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "brackets")

# Bias multipliers: direction -> magnitude -> multiplier
BIAS_MULTIPLIERS = {
    "over-picked": {"slight": 1.3, "moderate": 1.6, "heavy": 2.0},
    "under-picked": {"slight": 0.7, "moderate": 0.5, "heavy": 0.3},
}


def apply_biases(pick_pcts, biases):
    """Apply user bias multipliers to pick percentages.

    Args:
        pick_pcts: {team_name: {round: fraction}}
        biases: [{"team": name, "direction": "over-picked"|"under-picked", "magnitude": "slight"|...}]

    Returns:
        Modified pick_pcts dict (copy).
    """
    if not biases:
        return pick_pcts

    result = {}
    for team, rounds in pick_pcts.items():
        result[team] = dict(rounds)

    for bias in biases:
        team = bias.get("team", "")
        direction = bias.get("direction", "over-picked")
        magnitude = bias.get("magnitude", "slight")

        if team not in result:
            continue

        multiplier = BIAS_MULTIPLIERS.get(direction, {}).get(magnitude, 1.0)
        for rnd in result[team]:
            result[team][rnd] = max(0.01, min(0.99, result[team][rnd] * multiplier))

    return result


def resolve_scoring(job_config):
    """Resolve scoring from job config into a round_points dict."""
    preset = job_config.get("scoring_preset", "family")
    if preset == "custom":
        return {
            i: int(job_config.get(f"round_{i}_pts", config.ROUND_POINTS[i]))
            for i in range(1, 7)
        }
    return config.SCORING_PRESETS.get(preset, config.ROUND_POINTS)


def resolve_upset_config(job_config):
    """Parse upset bonus configuration from job config.

    Returns:
        (upset_mode, upset_values) tuple.
        upset_mode: "multiplier", "fixed", or None
        upset_values: {round: value} or None
    """
    upset_mode = job_config.get("upset_mode")
    if not upset_mode or upset_mode == "none":
        return None, None

    upset_values = {}
    for i in range(1, 7):
        key = f"upset_r{i}"
        val = job_config.get(key, 0)
        try:
            upset_values[i] = float(val)
        except (ValueError, TypeError):
            upset_values[i] = 0.0

    return upset_mode, upset_values


def run_optimization(job_id, job_config, conn):
    """Full optimization pipeline. Returns paths to generated files.

    Args:
        job_id: UUID string
        job_config: Parsed job configuration dict
        conn: Database connection
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load data from DB
    bracket_record = get_latest_bracket_record(conn)
    if not bracket_record:
        raise RuntimeError("No bracket data available. Upload a bracket first.")
    bracket_data = bracket_record["data"]
    bracket_year = bracket_record["year"]

    simulation_source = job_config.get("simulation_source", config.DEFAULT_SIMULATION_SOURCE)
    ratings = _load_simulation_ratings(conn, simulation_source, bracket_year)

    pick_sources = get_pick_sources(conn, year=bracket_record["year"])
    bracket_teams = extract_bracket_team_names(bracket_data)
    pick_pcts = build_consensus_pick_pcts(pick_sources, allowed_teams=bracket_teams)

    # Apply user biases
    biases = job_config.get("biases", [])
    pick_pcts = apply_biases(pick_pcts, biases)

    # Build bracket
    bracket = load_bracket_from_dict(bracket_data, ratings)

    # Resolve scoring
    round_points = resolve_scoring(job_config)

    # Run simulation
    n_sims = int(job_config.get("sims", config.DEFAULT_SIMULATIONS))
    reach_probs = simulate_tournament(bracket, n_sims=n_sims, seed=42, show_progress=False)

    # Run optimizer
    pool_size = int(job_config.get("pool_size", config.DEFAULT_POOL_SIZE))
    accuracy_weight = float(job_config.get("accuracy_weight", config.DEFAULT_ACCURACY_WEIGHT))
    force_champion = job_config.get("force_champion") or None
    upset_mode, upset_values = resolve_upset_config(job_config)

    optimized = optimize(
        bracket=bracket,
        reach_probs=reach_probs,
        pick_pcts=pick_pcts,
        pool_size=pool_size,
        accuracy_weight=accuracy_weight,
        n_sims=n_sims,
        force_champion=force_champion,
        round_points=round_points,
        quiet=True,
        upset_mode=upset_mode,
        upset_values=upset_values,
    )

    # Generate output files
    html_path = os.path.join(OUTPUT_DIR, f"{job_id}.html")
    json_path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
    config_path = os.path.join(OUTPUT_DIR, f"{job_id}.config")

    # HTML bracket
    export_bracket_html(optimized, html_path, reach_probs, pick_pcts,
                        title=f"Seed Money \u2014 Bracket {job_id[:8]}")

    # JSON data (serialized bracket + metadata)
    bracket_json = _serialize_bracket(optimized, reach_probs)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(bracket_json, f, indent=2)

    # Config file
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(job_config, f, indent=2)

    return {"html": html_path, "json": json_path, "config": config_path}


def _load_simulation_ratings(conn, simulation_source, year):
    """Load either a single ratings source or the cached consensus blend."""
    if simulation_source == "consensus":
        ratings_by_source = {}
        for source in config.RATING_SOURCE_WEIGHTS:
            ratings = get_latest_ratings(conn, source=source, year=year)
            if not ratings:
                ratings = get_latest_ratings(conn, source=source)
            if ratings:
                ratings_by_source[source] = ratings

        consensus = build_consensus_ratings(ratings_by_source)
        if consensus:
            return consensus

    ratings = get_latest_ratings(conn, source=simulation_source, year=year)
    if not ratings:
        ratings = get_latest_ratings(conn, source=simulation_source)
    if ratings:
        return ratings

    source_label = config.RATING_SOURCES.get(simulation_source, {}).get("label", simulation_source)
    if simulation_source == "consensus":
        raise RuntimeError(
            "No cached ratings available for the consensus blend. "
            "Run /admin/refresh so Torvik, KenPom, ESPN, and Neil Paine ratings are loaded."
        )
    raise RuntimeError(
        f"No {source_label} ratings available for simulation. "
        f"Run /admin/refresh with ratings_source={simulation_source} first."
    )


def _serialize_bracket(bracket, reach_probs):
    """Serialize a bracket to a JSON-friendly dict."""
    slots = {}
    for i in range(1, 128):
        team = bracket.slots[i]
        if team:
            slots[str(i)] = {
                "name": team.name,
                "seed": team.seed,
                "region": team.region,
            }

    champion = bracket.slots[1]
    champ_name = champion.name if champion else None

    return {
        "slots": slots,
        "champion": champ_name,
        "regions": bracket.regions,
        "reach_probs": {
            name: {str(r): p for r, p in rounds.items()}
            for name, rounds in reach_probs.items()
        } if reach_probs else {},
    }
