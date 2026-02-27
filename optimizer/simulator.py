"""Monte Carlo tournament simulator.

Simulates the tournament many times to compute the probability that each team
reaches each round. This is more accurate than analytical computation because
it naturally handles the conditional probabilities (who you face in round 2
depends on who won in round 1).
"""

import numpy as np
from tqdm import tqdm

from models.bracket import Bracket
from models.probability import log5
from models.team import Team


def simulate_tournament(bracket: Bracket, n_sims: int = 10_000,
                        seed: int | None = None,
                        show_progress: bool = True) -> dict[str, dict[int, float]]:
    """Run Monte Carlo simulation of the tournament.

    Args:
        bracket: The 64-team bracket with teams placed in starting slots
        n_sims: Number of simulations to run
        seed: Random seed for reproducibility
        show_progress: Show progress bar

    Returns:
        {team_name: {round: probability_of_reaching_that_round}}
        Round 1 = probability of being in tournament (always 1.0)
        Round 2 = probability of winning first game
        ...
        Round 7 = probability of winning championship
    """
    rng = np.random.default_rng(seed)

    # Initialize counters: how many times each team reaches each round
    reach_counts: dict[str, dict[int, int]] = {}
    for team in bracket.teams:
        reach_counts[team.name] = {r: 0 for r in range(1, 8)}  # rounds 1-7
        reach_counts[team.name][1] = n_sims  # everyone starts in round 1

    iterator = range(n_sims)
    if show_progress:
        iterator = tqdm(iterator, desc="Simulating tournaments")

    for _ in iterator:
        result = simulate_once(bracket, rng)
        for round_num, winners in result.items():
            for team in winners:
                reach_counts[team.name][round_num + 1] += 1

    # Convert counts to probabilities
    reach_probs: dict[str, dict[int, float]] = {}
    for name, counts in reach_counts.items():
        reach_probs[name] = {r: c / n_sims for r, c in counts.items()}

    return reach_probs


def simulate_once(bracket: Bracket, rng: np.random.Generator) -> dict[int, list[Team]]:
    """Simulate a single tournament.

    Returns:
        {round_num: [list of teams that won in that round]}
        Round 1 winners advance to round 2, etc.
    """
    # Work through the bracket from round 1 to round 6
    # We need a working copy of the bracket slots
    slots = list(bracket.slots)  # shallow copy is fine, Team objects are read-only here
    results: dict[int, list[Team]] = {}

    for round_num in range(1, 7):
        round_winners = []
        game_slots = bracket.get_all_game_slots_for_round(round_num)

        for game_slot in game_slots:
            left_slot, right_slot = bracket.get_matchup(game_slot)
            team_a = slots[left_slot]
            team_b = slots[right_slot]

            if team_a is None or team_b is None:
                # Shouldn't happen in a complete bracket
                winner = team_a or team_b
            else:
                p_a_wins = log5(team_a.rating, team_b.rating)
                winner = team_a if rng.random() < p_a_wins else team_b

            slots[game_slot] = winner
            round_winners.append(winner)

        results[round_num] = round_winners

    return results


def simulate_once_flat(bracket: Bracket, rng: np.random.Generator) -> list[Team | None]:
    """Simulate a single tournament and return the full slot array.

    Returns:
        A 128-element list where slots[1..63] contain game winners.
    """
    slots = list(bracket.slots)

    for round_num in range(1, 7):
        for game_slot in bracket.get_all_game_slots_for_round(round_num):
            left_slot, right_slot = bracket.get_matchup(game_slot)
            team_a = slots[left_slot]
            team_b = slots[right_slot]

            if team_a is None or team_b is None:
                slots[game_slot] = team_a or team_b
            else:
                p_a_wins = log5(team_a.rating, team_b.rating)
                slots[game_slot] = team_a if rng.random() < p_a_wins else team_b

    return slots
