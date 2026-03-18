"""Pool opponent modeling.

Generates simulated opponent brackets based on public pick percentages.
In a small pool (5-10 people), opponents are modeled as "typical" bracket
fillers who roughly follow public sentiment with some noise.

Opponent brackets use correlated picks: once a team is picked to win a game,
that same team is carried forward into later rounds (as a real bracket filler
would). The per-round pick data influences the initial decision, but subsequent
rounds inherit the earlier pick with a consistency bias.
"""

import numpy as np

from models.bracket import Bracket
from models.probability import log5
from models.team import Team
from optimizer.pick_utils import get_pick_pct


def generate_opponent_bracket(bracket: Bracket, pick_pcts: dict[str, dict[int, float]],
                              rng: np.random.Generator) -> Bracket:
    """Generate a single simulated opponent bracket with correlated picks.

    The opponent picks each game winner based on public pick percentages,
    but with bracket-coherent correlation: once a team is picked to advance,
    that team is the one competing in the next round (just like a real person
    filling out a bracket). The pick probability for later rounds still uses
    the public pick data for that specific round, but the candidate teams are
    constrained by earlier picks.

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

            # Determine pick probability for team_a in this specific matchup.
            # Because we fill round-by-round and propagate winners, team_a and
            # team_b are already the teams this opponent "advanced" from prior
            # rounds, giving natural bracket correlation.
            p_pick_a = _get_pick_prob(team_a, team_b, round_num, pick_pcts)

            opp.slots[game_slot] = team_a if rng.random() < p_pick_a else team_b

    return opp


def _get_pick_prob(team_a: Team, team_b: Team, round_num: int,
                   pick_pcts: dict[str, dict[int, float]]) -> float:
    """Get the probability that a typical bracket picker chooses team_a over team_b.

    Uses pick percentage data when available, falls back to seed-based heuristic.
    """
    pct_a = get_pick_pct(pick_pcts, team_a.name, round_num + 1, -1.0)
    pct_b = get_pick_pct(pick_pcts, team_b.name, round_num + 1, -1.0)

    if pct_a >= 0 and pct_b >= 0 and (pct_a + pct_b) > 0:
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
