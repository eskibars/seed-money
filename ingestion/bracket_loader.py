"""Bracket loader - define the 64-team bracket structure.

Supports:
1. Interactive CLI entry
2. JSON file input
3. Programmatic construction
"""

import json
import os

import config
from models.bracket import Bracket, SEED_ORDER
from models.team import Team


def load_bracket_interactive(ratings: dict[str, dict] | None = None) -> Bracket:
    """Interactively enter the 64-team bracket via CLI prompts.

    Args:
        ratings: Optional pre-loaded team ratings to auto-fill power ratings
    """
    bracket = Bracket()

    print("\n=== BRACKET ENTRY ===")
    print("Enter teams for each region. Use standard team names.")
    print("Seeds are entered in order: 1, 16, 8, 9, 5, 12, 4, 13, 6, 11, 3, 14, 7, 10, 2, 15\n")

    for region_idx in range(4):
        region_name = input(f"Region {region_idx + 1} name [{config.REGION_NAMES[region_idx]}]: ").strip()
        if not region_name:
            region_name = config.REGION_NAMES[region_idx]

        teams_by_seed = {}
        for seed in SEED_ORDER:
            name = input(f"  {region_name} #{seed} seed: ").strip()
            if not name:
                continue

            # Look up rating if available
            rating_info = _lookup_rating(name, ratings) if ratings else {}

            team = Team(
                name=name,
                seed=seed,
                region=region_name,
                rating=rating_info.get("rating", _default_rating_for_seed(seed)),
                adj_offense=rating_info.get("adj_offense", 100.0),
                adj_defense=rating_info.get("adj_defense", 100.0),
            )
            teams_by_seed[seed] = team

        bracket.set_teams_for_region(region_idx, region_name, teams_by_seed)
        print(f"  -> {region_name} loaded with {len(teams_by_seed)} teams\n")

    return bracket


def load_bracket_from_json(filepath: str, ratings: dict[str, dict] | None = None) -> Bracket:
    """Load bracket from a JSON file.

    Expected format:
    {
        "regions": [
            {
                "name": "East",
                "teams": {"1": "Duke", "2": "Alabama", ..., "16": "Norfolk St."}
            },
            ...
        ]
    }
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    bracket = Bracket()

    for region_idx, region_data in enumerate(data["regions"]):
        region_name = region_data["name"]
        teams_by_seed = {}

        for seed_str, name in region_data["teams"].items():
            seed = int(seed_str)
            rating_info = _lookup_rating(name, ratings) if ratings else {}

            team = Team(
                name=name,
                seed=seed,
                region=region_name,
                rating=rating_info.get("rating", _default_rating_for_seed(seed)),
                adj_offense=rating_info.get("adj_offense", 100.0),
                adj_defense=rating_info.get("adj_defense", 100.0),
            )
            teams_by_seed[seed] = team

        bracket.set_teams_for_region(region_idx, region_name, teams_by_seed)

    print(f"Loaded bracket from {filepath}: {len(bracket.teams)} teams in {len(bracket.regions)} regions")
    return bracket


def save_bracket_to_json(bracket: Bracket, filepath: str):
    """Save a bracket's team placements to JSON."""
    data = {"regions": []}

    for region_idx in range(4):
        region_name = bracket.regions.get(region_idx, f"Region {region_idx + 1}")
        teams = {}

        base = 64 + region_idx * 16
        for pos in range(16):
            team = bracket.slots[base + pos]
            if team:
                teams[str(team.seed)] = team.name

        data["regions"].append({"name": region_name, "teams": teams})

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved bracket to {filepath}")


def _lookup_rating(name: str, ratings: dict[str, dict] | None) -> dict:
    """Look up a team's rating, trying exact match then fuzzy match."""
    if not ratings:
        return {}

    # Exact match
    if name in ratings:
        return ratings[name]

    # Try aliases
    aliases_path = os.path.join(os.path.dirname(__file__), "..", "data", "team_aliases.json")
    if os.path.exists(aliases_path):
        with open(aliases_path, "r") as f:
            aliases = json.load(f)
        canonical = aliases.get(name, name)
        if canonical in ratings:
            return ratings[canonical]

    # Fuzzy match
    from difflib import get_close_matches
    matches = get_close_matches(name, ratings.keys(), n=1, cutoff=0.7)
    if matches:
        return ratings[matches[0]]

    return {}


def _default_rating_for_seed(seed: int) -> float:
    """Provide a reasonable default rating based on seed when no data is available."""
    # Rough historical Barthag equivalents by seed
    defaults = {
        1: 0.95, 2: 0.92, 3: 0.89, 4: 0.86, 5: 0.83, 6: 0.80,
        7: 0.77, 8: 0.74, 9: 0.72, 10: 0.70, 11: 0.68, 12: 0.65,
        13: 0.55, 14: 0.45, 15: 0.35, 16: 0.25,
    }
    return defaults.get(seed, 0.50)
