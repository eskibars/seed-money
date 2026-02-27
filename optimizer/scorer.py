"""Bracket scoring.

Scores a filled bracket against actual tournament results using
the custom family pool scoring system.
"""

import config
from models.bracket import Bracket
from models.team import Team


def score_bracket(picks: Bracket, actual: list[Team | None]) -> int:
    """Score a bracket against actual tournament results.

    Args:
        picks: A filled bracket (all 63 game slots populated with picks)
        actual: A 128-element slot array from a simulated (or real) tournament

    Returns:
        Total score
    """
    total = 0

    for game_slot in range(1, 64):
        round_num = picks.get_round(game_slot)
        points = config.ROUND_POINTS[round_num]

        picked_team = picks.slots[game_slot]
        actual_team = actual[game_slot]

        if picked_team is not None and actual_team is not None:
            if picked_team == actual_team:
                total += points

    return total


def score_bracket_by_round(picks: Bracket, actual: list[Team | None]) -> dict[int, int]:
    """Score a bracket broken down by round.

    Returns:
        {round_num: points_earned}
    """
    by_round = {r: 0 for r in range(1, 7)}

    for game_slot in range(1, 64):
        round_num = picks.get_round(game_slot)
        points = config.ROUND_POINTS[round_num]

        picked_team = picks.slots[game_slot]
        actual_team = actual[game_slot]

        if picked_team is not None and actual_team is not None:
            if picked_team == actual_team:
                by_round[round_num] += points

    return by_round
