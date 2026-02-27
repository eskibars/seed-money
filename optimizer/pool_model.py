"""Pool opponent modeling.

Generates simulated opponent brackets based on public pick percentages.
In a small pool (5-10 people), opponents are modeled as "typical" bracket
fillers who roughly follow public sentiment with some noise.
"""

import numpy as np

from models.bracket import Bracket
from models.probability import log5
from models.team import Team


def generate_opponent_bracket(bracket: Bracket, pick_pcts: dict[str, dict[int, float]],
                              rng: np.random.Generator) -> Bracket:
    """Generate a single simulated opponent bracket.

    The opponent picks each game winner based on public pick percentages.
    When per-round pick data is available, use it directly.
    When only championship pick data is available, fall back to rating-biased picks.

    Args:
        bracket: The base 64-team bracket (teams in starting slots)
        pick_pcts: {team_name: {round: pick_fraction}}
        rng: Random number generator
    """
    opp = bracket.copy()

    for round_num in range(1, 7):
        for game_slot in opp.get_all_game_slots_for_round(round_num):
            left_slot, right_slot = opp.get_matchup(game_slot)
            team_a = opp.slots[left_slot]
            team_b = opp.slots[right_slot]

            if team_a is None or team_b is None:
                opp.slots[game_slot] = team_a or team_b
                continue

            # Determine pick probability for team_a
            p_pick_a = _get_pick_prob(team_a, team_b, round_num, pick_pcts)

            opp.slots[game_slot] = team_a if rng.random() < p_pick_a else team_b

    return opp


def _get_pick_prob(team_a: Team, team_b: Team, round_num: int,
                   pick_pcts: dict[str, dict[int, float]]) -> float:
    """Get the probability that a typical bracket picker chooses team_a over team_b.

    Uses pick percentage data when available, falls back to seed-based heuristic.
    """
    pcts_a = pick_pcts.get(team_a.name, {})
    pcts_b = pick_pcts.get(team_b.name, {})

    pct_a = pcts_a.get(round_num + 1)  # pick_pcts are indexed by "reaching round N"
    pct_b = pcts_b.get(round_num + 1)  # so round_num game winners "reach" round_num+1

    if pct_a is not None and pct_b is not None and (pct_a + pct_b) > 0:
        # Normalize to get head-to-head pick probability
        return pct_a / (pct_a + pct_b)

    # Fallback: public tends to pick the better seed (lower number)
    # with some probability. This models the "chalk" tendency.
    if team_a.seed != team_b.seed:
        # Stronger seed gets picked ~70-90% of the time depending on gap
        seed_gap = team_b.seed - team_a.seed  # positive = A is better seed
        chalk_prob = 0.5 + 0.03 * seed_gap  # rough approximation
        return max(0.15, min(0.85, chalk_prob))

    # Same seed (can happen in later rounds): use rating
    return log5(team_a.rating, team_b.rating)
