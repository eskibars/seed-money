"""Bracket scoring.

Scores a filled bracket against actual tournament results using
configurable scoring systems including upset bonuses.
"""

import config
from models.bracket import Bracket
from models.team import Team


def _compute_upset_bonus(winner: Team, loser: Team, round_num: int,
                         upset_mode: str | None,
                         upset_values: dict[int, float] | None) -> float:
    """Compute upset bonus points for a correct pick.

    An upset occurs when the winner has a higher seed number (lower rank)
    than the loser.

    Args:
        winner: The team that won the game
        loser: The team that lost the game
        round_num: Which round this game is in
        upset_mode: "multiplier" or "fixed" (or None for no bonus)
        upset_values: {round: value} — multiplier or fixed bonus per round

    Returns:
        Bonus points (0 if not an upset or no upset mode)
    """
    if not upset_mode or not upset_values:
        return 0.0

    # Upset = winner has higher seed number (e.g. 12-seed beats 5-seed)
    if winner.seed <= loser.seed:
        return 0.0

    seed_diff = winner.seed - loser.seed
    round_val = upset_values.get(round_num, 0.0)

    if upset_mode == "multiplier":
        return seed_diff * round_val
    elif upset_mode == "fixed":
        return round_val
    return 0.0


def compute_game_points(winner: Team, loser: Team, round_num: int,
                        round_points: dict[int, int] | None = None,
                        upset_mode: str | None = None,
                        upset_values: dict[int, float] | None = None) -> float:
    """Compute total points for a correct pick in a specific game.

    Args:
        winner: The winning team
        loser: The losing team
        round_num: Which round
        round_points: Base points per round
        upset_mode: "multiplier", "fixed", or None
        upset_values: Per-round upset bonus values

    Returns:
        Base points + upset bonus
    """
    rp = round_points or config.ROUND_POINTS
    base = rp[round_num]
    bonus = _compute_upset_bonus(winner, loser, round_num, upset_mode, upset_values)
    return base + bonus


def score_bracket(picks: Bracket, actual: list[Team | None],
                  round_points: dict[int, int] | None = None,
                  upset_mode: str | None = None,
                  upset_values: dict[int, float] | None = None) -> float:
    """Score a bracket against actual tournament results.

    Args:
        picks: A filled bracket (all 63 game slots populated with picks)
        actual: A 128-element slot array from a simulated (or real) tournament
        round_points: Optional custom scoring per round (defaults to config.ROUND_POINTS)
        upset_mode: "multiplier" or "fixed" (or None for no bonus)
        upset_values: {round: value} — multiplier or fixed bonus per round

    Returns:
        Total score (may be float if upset bonuses produce fractional values)
    """
    rp = round_points or config.ROUND_POINTS
    total = 0.0

    for game_slot in range(1, 64):
        round_num = picks.get_round(game_slot)

        picked_team = picks.slots[game_slot]
        actual_team = actual[game_slot]

        if picked_team is not None and actual_team is not None:
            if picked_team == actual_team:
                # Find the loser from the actual bracket
                left_child = 2 * game_slot
                right_child = 2 * game_slot + 1
                if left_child < 128 and right_child < 128:
                    left_team = actual[left_child]
                    right_team = actual[right_child]
                    if left_team and right_team:
                        loser = right_team if actual_team == left_team else left_team
                        total += compute_game_points(
                            actual_team, loser, round_num, rp, upset_mode, upset_values
                        )
                    else:
                        total += rp[round_num]
                else:
                    total += rp[round_num]

    return total


def score_bracket_by_round(picks: Bracket, actual: list[Team | None],
                           round_points: dict[int, int] | None = None,
                           upset_mode: str | None = None,
                           upset_values: dict[int, float] | None = None) -> dict[int, float]:
    """Score a bracket broken down by round.

    Returns:
        {round_num: points_earned}
    """
    rp = round_points or config.ROUND_POINTS
    by_round: dict[int, float] = {r: 0.0 for r in range(1, 7)}

    for game_slot in range(1, 64):
        round_num = picks.get_round(game_slot)

        picked_team = picks.slots[game_slot]
        actual_team = actual[game_slot]

        if picked_team is not None and actual_team is not None:
            if picked_team == actual_team:
                left_child = 2 * game_slot
                right_child = 2 * game_slot + 1
                if left_child < 128 and right_child < 128:
                    left_team = actual[left_child]
                    right_team = actual[right_child]
                    if left_team and right_team:
                        loser = right_team if actual_team == left_team else left_team
                        by_round[round_num] += compute_game_points(
                            actual_team, loser, round_num, rp, upset_mode, upset_values
                        )
                    else:
                        by_round[round_num] += rp[round_num]
                else:
                    by_round[round_num] += rp[round_num]

    return by_round
