"""Core bracket optimization engine.

Uses a two-phase approach:
1. Exhaustive search over late-round picks (Final Four + Championship)
2. Greedy backward fill for earlier rounds

The goal is to maximize the probability of winning a small family pool,
not just maximizing expected points.
"""

from itertools import product

import numpy as np
from tqdm import tqdm

import config
from models.bracket import Bracket
from models.probability import log5
from models.team import Team
from optimizer.scorer import score_bracket
from optimizer.simulator import simulate_once_flat
from optimizer.pool_model import generate_opponent_bracket


def optimize(bracket: Bracket,
             reach_probs: dict[str, dict[int, float]],
             pick_pcts: dict[str, dict[int, float]],
             pool_size: int = config.DEFAULT_POOL_SIZE,
             accuracy_weight: float = config.DEFAULT_ACCURACY_WEIGHT,
             n_sims: int = config.DEFAULT_SIMULATIONS,
             seed: int | None = 42,
             force_champion: str | None = None) -> Bracket:
    """Optimize a bracket for maximum pool win probability.

    Args:
        bracket: The 64-team bracket with teams in starting slots
        reach_probs: {team_name: {round: probability}} from Monte Carlo sim
        pick_pcts: {team_name: {round: pick_fraction}} public pick data
        pool_size: Number of people in the pool
        accuracy_weight: 0-1, how much to weight accuracy vs contrarianism
        n_sims: Number of simulations for validation
        seed: Random seed
        force_champion: Force a specific team as champion (optional)

    Returns:
        Optimized bracket with all 63 game slots filled
    """
    rng = np.random.default_rng(seed)
    result = bracket.copy()

    # Phase 1: Find optimal late-round picks (F4 + Championship)
    print("\n=== Phase 1: Optimizing Final Four and Championship ===")
    late_round_config = _optimize_late_rounds(
        bracket, reach_probs, pick_pcts, pool_size, accuracy_weight,
        rng, n_sims, force_champion
    )

    champion, f4_teams, semi_winners = late_round_config
    print(f"  Champion: {champion}")
    print(f"  Final Four: {', '.join(str(t) for t in f4_teams)}")

    # Phase 2: Fill the bracket forward with late-round constraints
    print("\n=== Phase 2: Filling bracket ===")
    result = _fill_bracket_forward(
        result, champion, f4_teams, semi_winners,
        reach_probs, pick_pcts, pool_size, accuracy_weight
    )

    # Phase 3: Validate with Monte Carlo
    print(f"\n=== Phase 3: Validating ({n_sims} simulations) ===")
    win_rate, avg_score = _validate(result, bracket, pick_pcts, pool_size, n_sims, rng)

    baseline = 1.0 / pool_size
    print(f"  Expected pool win rate: {win_rate:.1%} (baseline: {baseline:.1%})")
    print(f"  Advantage: {win_rate / baseline:.1f}x over random")
    print(f"  Expected score: {avg_score:.1f} / {config.MAX_SCORE}")

    return result


def _optimize_late_rounds(bracket, reach_probs, pick_pcts, pool_size,
                          accuracy_weight, rng, n_sims, force_champion):
    """Exhaustive search over Final Four + Championship combinations."""

    # Get top candidates per region
    candidates_per_region = []
    for region_idx in range(4):
        region_teams = []
        base = 64 + region_idx * 16
        for pos in range(16):
            team = bracket.slots[base + pos]
            if team:
                # Score by probability of reaching Final Four (round 5)
                p_f4 = reach_probs.get(team.name, {}).get(5, 0.0)
                region_teams.append((team, p_f4))

        region_teams.sort(key=lambda x: x[1], reverse=True)
        top_n = config.F4_CANDIDATES_PER_REGION
        candidates_per_region.append([t for t, _ in region_teams[:top_n]])

    # Pre-simulate tournaments for fast evaluation
    pre_sims = min(2000, n_sims)
    print(f"  Pre-simulating {pre_sims} tournaments for evaluation...")
    sim_results = []
    for _ in range(pre_sims):
        sim_results.append(simulate_once_flat(bracket, rng))

    # Enumerate all F4 combos x semifinal winners x champion
    # Regions 0,1 feed into semifinal at slot 2; regions 2,3 feed into semifinal at slot 3
    best_score = -1
    best_config = None
    total_combos = 0

    for f4 in product(*candidates_per_region):
        if force_champion and not any(t.name == force_champion for t in f4):
            continue

        # Semifinal 1: region 0 vs region 1
        for semi1_winner in [f4[0], f4[1]]:
            # Semifinal 2: region 2 vs region 3
            for semi2_winner in [f4[2], f4[3]]:
                for champ in [semi1_winner, semi2_winner]:
                    if force_champion and champ.name != force_champion:
                        continue

                    total_combos += 1
                    score = _quick_eval_late_rounds(
                        f4, semi1_winner, semi2_winner, champ,
                        reach_probs, pick_pcts, pool_size, accuracy_weight
                    )
                    if score > best_score:
                        best_score = score
                        best_config = (champ, list(f4), [semi1_winner, semi2_winner])

    print(f"  Evaluated {total_combos} late-round combinations")

    if best_config is None:
        # Fallback: pick highest-rated team
        all_teams = bracket.teams[:]
        all_teams.sort(key=lambda t: t.rating, reverse=True)
        champ = all_teams[0]
        # Pick best per region for F4
        f4 = []
        for region_idx in range(4):
            best = max(candidates_per_region[region_idx], key=lambda t: t.rating)
            f4.append(best)
        best_config = (champ, f4, [f4[0], f4[2]])

    return best_config


def _quick_eval_late_rounds(f4_teams, semi1_winner, semi2_winner, champion,
                            reach_probs, pick_pcts, pool_size, accuracy_weight):
    """Quick scoring of a late-round configuration using EMV formula.

    No simulation needed — uses pre-computed reach probabilities.
    """
    total_emv = 0.0

    # Score Final Four picks (round 5, 4 pts each)
    for team in f4_teams:
        p_reach = reach_probs.get(team.name, {}).get(5, 0.0)
        pick_frac = pick_pcts.get(team.name, {}).get(5, _default_pick_pct(team.seed, 5))
        emv = _compute_emv(p_reach, pick_frac, config.ROUND_POINTS[4], pool_size, accuracy_weight)
        total_emv += emv

    # Score semifinal winners (round 5, 4 pts each)
    for team in [semi1_winner, semi2_winner]:
        p_reach = reach_probs.get(team.name, {}).get(6, 0.0)
        pick_frac = pick_pcts.get(team.name, {}).get(6, _default_pick_pct(team.seed, 6))
        emv = _compute_emv(p_reach, pick_frac, config.ROUND_POINTS[5], pool_size, accuracy_weight)
        total_emv += emv

    # Score champion (round 6, 5 pts)
    p_champ = reach_probs.get(champion.name, {}).get(7, 0.0)
    champ_pick = pick_pcts.get(champion.name, {}).get(7, _default_pick_pct(champion.seed, 7))
    emv = _compute_emv(p_champ, champ_pick, config.ROUND_POINTS[6], pool_size, accuracy_weight)
    total_emv += emv

    return total_emv


def _compute_emv(p_reach: float, pick_frac: float, points: int,
                 pool_size: int, accuracy_weight: float) -> float:
    """Compute Expected Marginal Value for a pick.

    EMV = P(reach) * points * [alpha + (1-alpha) * (1-pick_frac)^(pool_size-1)]

    The (1-pick_frac)^(pool_size-1) term is the probability that none of your
    opponents also made this pick — i.e., it's a "unique" correct pick.
    """
    if p_reach <= 0:
        return 0.0

    p_unique = (1 - pick_frac) ** (pool_size - 1)
    advantage = accuracy_weight + (1 - accuracy_weight) * p_unique
    return p_reach * points * advantage


def _fill_bracket_forward(result, champion, f4_teams, semi_winners,
                          reach_probs, pick_pcts, pool_size, accuracy_weight):
    """Fill the bracket working forward from round 1, with late-round picks as constraints.

    1. Determine which teams are "forced" (must win every game on their path)
    2. Fill round by round from R64 to Championship
    3. For forced games, pick the forced team; for free games, pick by EMV
    """
    # Set the late-round results
    result.set_winner(1, champion)
    result.set_winner(2, semi_winners[0])
    result.set_winner(3, semi_winners[1])
    for i, team in enumerate(f4_teams):
        result.set_winner(4 + i, team)

    # Build set of forced teams and their required game slots
    forced_teams = set()
    forced_teams.add(champion)
    for t in f4_teams:
        forced_teams.add(t)

    # Map each forced team to the set of game slots they must win
    forced_slots: dict[str, set[int]] = {}
    for team in forced_teams:
        starting = result.get_starting_slot(team)
        if starting is None:
            continue
        path = result.get_path_to_championship(starting)
        forced_slots[team.name] = set(path)

    # Fill forward: round 1 (slot 32-63), round 2 (16-31), round 3 (8-15)
    # Rounds 4-6 are already filled by the late-round picks
    for round_num in range(1, 4):
        for game_slot in result.get_all_game_slots_for_round(round_num):
            left_slot, right_slot = result.get_matchup(game_slot)
            team_a = result.slots[left_slot]
            team_b = result.slots[right_slot]

            if team_a is None and team_b is None:
                continue
            if team_a is None:
                result.set_winner(game_slot, team_b)
                continue
            if team_b is None:
                result.set_winner(game_slot, team_a)
                continue

            # Check if either team is forced to win this game
            a_forced = game_slot in forced_slots.get(team_a.name, set())
            b_forced = game_slot in forced_slots.get(team_b.name, set())

            if a_forced:
                result.set_winner(game_slot, team_a)
            elif b_forced:
                result.set_winner(game_slot, team_b)
            else:
                # Free pick: use EMV
                emv_a = _compute_emv(
                    reach_probs.get(team_a.name, {}).get(round_num + 1, 0.0),
                    pick_pcts.get(team_a.name, {}).get(round_num + 1, _default_pick_pct(team_a.seed, round_num + 1)),
                    config.ROUND_POINTS[round_num],
                    pool_size, accuracy_weight
                )
                emv_b = _compute_emv(
                    reach_probs.get(team_b.name, {}).get(round_num + 1, 0.0),
                    pick_pcts.get(team_b.name, {}).get(round_num + 1, _default_pick_pct(team_b.seed, round_num + 1)),
                    config.ROUND_POINTS[round_num],
                    pool_size, accuracy_weight
                )

                result.set_winner(game_slot, team_a if emv_a >= emv_b else team_b)

    return result


def _validate(picks, bracket, pick_pcts, pool_size, n_sims, rng):
    """Validate the bracket via Monte Carlo simulation against opponent pool."""
    wins = 0
    total_score = 0

    for _ in tqdm(range(n_sims), desc="Validating"):
        # Simulate actual tournament outcome
        actual = simulate_once_flat(bracket, rng)

        # Score our bracket
        my_score = score_bracket(picks, actual)
        total_score += my_score

        # Simulate opponents
        max_opp_score = 0
        for _ in range(pool_size - 1):
            opp = generate_opponent_bracket(bracket, pick_pcts, rng)
            opp_score = score_bracket(opp, actual)
            max_opp_score = max(max_opp_score, opp_score)

        if my_score > max_opp_score:
            wins += 1

    return wins / n_sims, total_score / n_sims


def _default_pick_pct(seed: int, round_reaching: int) -> float:
    """Default pick percentage when no data is available.

    Based on rough historical patterns of how the public picks.
    """
    # Approximate public pick rates by seed and round
    # Round 2 = winning first game, Round 7 = winning championship
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
