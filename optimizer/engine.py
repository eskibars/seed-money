"""Core bracket optimization engine.

Uses a two-phase approach:
1. Exhaustive search over late-round picks (Final Four + Championship)
2. Greedy forward fill for earlier rounds

The goal is to maximize the probability of winning a small family pool,
not just maximizing expected points.
"""

from itertools import product

import numpy as np
from tqdm import tqdm

import config
from models.bracket import Bracket
from models.team import Team
from optimizer.pick_utils import get_pick_pct
from optimizer.scorer import score_bracket, compute_game_points
from optimizer.simulator import simulate_once_flat
from optimizer.pool_model import generate_opponent_bracket

LATE_ROUND_SLOTS = (4, 5, 6, 7, 2, 3, 1)
LATE_ROUND_NUMBERS = {4: 4, 5: 4, 6: 4, 7: 4, 2: 5, 3: 5, 1: 6}


def optimize(bracket: Bracket,
             reach_probs: dict[str, dict[int, float]],
             pick_pcts: dict[str, dict[int, float]],
             pool_size: int = config.DEFAULT_POOL_SIZE,
             accuracy_weight: float = config.DEFAULT_ACCURACY_WEIGHT,
             n_sims: int = config.DEFAULT_SIMULATIONS,
             seed: int | None = 42,
             force_champion: str | None = None,
             round_points: dict[int, int] | None = None,
             quiet: bool = False,
             upset_mode: str | None = None,
             upset_values: dict[int, float] | None = None) -> Bracket:
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
        round_points: Custom scoring per round (defaults to config.ROUND_POINTS)
        quiet: Suppress print output (for web usage)
        upset_mode: "multiplier" or "fixed" (or None for no upset bonus)
        upset_values: {round: value} — per-round multiplier or fixed bonus

    Returns:
        Optimized bracket with all 63 game slots filled
    """
    rp = round_points or config.ROUND_POINTS
    rng = np.random.default_rng(seed)
    result = bracket.copy()

    def _print(msg):
        if not quiet:
            print(msg)

    # Phase 1: Find optimal late-round picks (F4 + Championship)
    _print("\n=== Phase 1: Optimizing Final Four and Championship ===")
    late_round_config = _optimize_late_rounds(
        bracket, reach_probs, pick_pcts, pool_size, accuracy_weight,
        rng, n_sims, force_champion, rp, quiet, upset_mode, upset_values
    )

    champion, f4_teams, semi_winners = late_round_config
    _print(f"  Champion: {champion}")
    _print(f"  Final Four: {', '.join(str(t) for t in f4_teams)}")

    # Phase 2: Fill the bracket forward with late-round constraints
    _print("\n=== Phase 2: Filling bracket ===")
    result = _fill_bracket_forward(
        result, champion, f4_teams, semi_winners,
        reach_probs, pick_pcts, pool_size, accuracy_weight, rp,
        upset_mode, upset_values
    )

    # Phase 3: Validate with Monte Carlo
    _print(f"\n=== Phase 3: Validating ({n_sims} simulations) ===")
    win_rate, avg_score = _validate(
        result, bracket, pick_pcts, pool_size, n_sims, rng, rp, quiet,
        upset_mode, upset_values
    )

    max_score = sum(config.GAMES_PER_ROUND[r] * rp[r] for r in range(1, 7))
    baseline = 1.0 / pool_size
    _print(f"  Expected pool win rate: {win_rate:.1%} (baseline: {baseline:.1%})")
    _print(f"  Advantage: {win_rate / baseline:.1f}x over random")
    _print(f"  Expected score: {avg_score:.1f} / {max_score} (base pts, excl. upset bonus)")

    return result


def _optimize_late_rounds(bracket, reach_probs, pick_pcts, pool_size,
                          accuracy_weight, rng, n_sims, force_champion,
                          round_points=None, quiet=False,
                          upset_mode=None, upset_values=None):
    """Exhaustive search over Final Four + Championship combinations."""
    rp = round_points or config.ROUND_POINTS

    def _print(msg):
        if not quiet:
            print(msg)

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

    # Pre-simulate tournaments for fast, path-aware late-round evaluation
    pre_sims = min(1000, n_sims)
    _print(f"  Pre-simulating {pre_sims} tournaments for evaluation...")
    sim_results = [simulate_once_flat(bracket, rng) for _ in range(pre_sims)]
    slot_candidates = _build_late_round_slot_candidates(candidates_per_region)
    score_cache = _precompute_late_round_scores(
        sim_results, slot_candidates, rp, upset_mode, upset_values
    )
    opp_max_scores = _precompute_opponent_late_round_scores(
        bracket, pick_pcts, pool_size, sim_results, rng, rp, upset_mode, upset_values
    )

    # Enumerate all F4 combos x semifinal winners x champion
    # Regions 0,1 feed into semifinal at slot 2; regions 2,3 feed into semifinal at slot 3
    best_score = None
    best_config = None
    total_combos = 0

    for f4 in product(*candidates_per_region):
        if force_champion and not any(t.name == force_champion for t in f4):
            continue

        # Semifinal 1: region 0 vs region 1
        for semi1_winner in [f4[0], f4[1]]:
            semi1_loser = f4[1] if semi1_winner == f4[0] else f4[0]
            # Semifinal 2: region 2 vs region 3
            for semi2_winner in [f4[2], f4[3]]:
                semi2_loser = f4[3] if semi2_winner == f4[2] else f4[2]
                for champ in [semi1_winner, semi2_winner]:
                    if force_champion and champ.name != force_champion:
                        continue
                    champ_loser = semi2_winner if champ == semi1_winner else semi1_winner

                    total_combos += 1
                    late_win_rate, avg_late_score = _evaluate_late_rounds_from_sims(
                        f4, semi1_winner, semi2_winner, champ, score_cache, opp_max_scores, pool_size
                    )
                    heuristic_score = _quick_eval_late_rounds(
                        f4, semi1_winner, semi1_loser,
                        semi2_winner, semi2_loser,
                        champ, champ_loser,
                        reach_probs, pick_pcts, pool_size, accuracy_weight, rp,
                        upset_mode, upset_values
                    )
                    score = (
                        late_win_rate,
                        accuracy_weight * avg_late_score + (1 - accuracy_weight) * heuristic_score,
                        heuristic_score,
                    )
                    if best_score is None or score > best_score:
                        best_score = score
                        best_config = (champ, list(f4), [semi1_winner, semi2_winner])

    _print(f"  Evaluated {total_combos} late-round combinations")

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


def _quick_eval_late_rounds(f4_teams, semi1_winner, semi1_loser,
                            semi2_winner, semi2_loser,
                            champion, champ_loser,
                            reach_probs, pick_pcts, pool_size, accuracy_weight,
                            round_points=None,
                            upset_mode=None, upset_values=None):
    """Quick scoring of a late-round configuration using EMV formula.

    No simulation needed — uses pre-computed reach probabilities.
    Now accounts for upset bonuses in semifinal and championship matchups.
    """
    rp = round_points or config.ROUND_POINTS
    total_emv = 0.0

    # Score Final Four picks (round 4 = Elite Eight, scoring for reaching F4)
    # No upset bonus here since we don't know the exact E8 matchup opponent
    for team in f4_teams:
        p_reach = _conditional_advance_prob(team, 4, reach_probs)
        pick_frac = get_pick_pct(pick_pcts, team.name, 5, _default_pick_pct(team.seed, 5))
        emv = _compute_emv(p_reach, pick_frac, rp[4], pool_size, accuracy_weight)
        total_emv += emv

    # Score semifinal winners (round 5 = Final Four)
    for winner, loser in [(semi1_winner, semi1_loser), (semi2_winner, semi2_loser)]:
        p_reach = _conditional_advance_prob(winner, 5, reach_probs)
        pick_frac = get_pick_pct(pick_pcts, winner.name, 6, _default_pick_pct(winner.seed, 6))
        pts = compute_game_points(winner, loser, 5, rp, upset_mode, upset_values)
        emv = _compute_emv(p_reach, pick_frac, pts, pool_size, accuracy_weight)
        total_emv += emv

    # Score champion (round 6 = Championship)
    p_champ = _conditional_advance_prob(champion, 6, reach_probs)
    champ_pick = get_pick_pct(pick_pcts, champion.name, 7, _default_pick_pct(champion.seed, 7))
    champ_pts = compute_game_points(champion, champ_loser, 6, rp, upset_mode, upset_values)
    emv = _compute_emv(p_champ, champ_pick, champ_pts, pool_size, accuracy_weight)
    total_emv += emv

    return total_emv


def _compute_emv(p_reach: float, pick_frac: float, points: float,
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
                          reach_probs, pick_pcts, pool_size, accuracy_weight,
                          round_points=None,
                          upset_mode=None, upset_values=None):
    """Fill the bracket working forward from round 1, with late-round picks as constraints.

    1. Determine which teams are "forced" (must win every game on their path)
    2. Fill round by round from R64 to Championship
    3. For forced games, pick the forced team; for free games, pick by EMV
    """
    rp = round_points or config.ROUND_POINTS
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
                # Free pick: use EMV with upset-aware points
                pts_a = compute_game_points(
                    team_a, team_b, round_num, rp, upset_mode, upset_values
                )
                pts_b = compute_game_points(
                    team_b, team_a, round_num, rp, upset_mode, upset_values
                )

                emv_a = _compute_emv(
                    _conditional_advance_prob(team_a, round_num, reach_probs),
                    get_pick_pct(pick_pcts, team_a.name, round_num + 1, _default_pick_pct(team_a.seed, round_num + 1)),
                    pts_a,
                    pool_size, accuracy_weight
                )
                emv_b = _compute_emv(
                    _conditional_advance_prob(team_b, round_num, reach_probs),
                    get_pick_pct(pick_pcts, team_b.name, round_num + 1, _default_pick_pct(team_b.seed, round_num + 1)),
                    pts_b,
                    pool_size, accuracy_weight
                )

                result.set_winner(game_slot, team_a if emv_a >= emv_b else team_b)

    return result


def _validate(picks, bracket, pick_pcts, pool_size, n_sims, rng,
              round_points=None, quiet=False,
              upset_mode=None, upset_values=None):
    """Validate the bracket via Monte Carlo simulation against opponent pool."""
    rp = round_points or config.ROUND_POINTS
    wins = 0
    total_score = 0

    iterator = tqdm(range(n_sims), desc="Validating") if not quiet else range(n_sims)
    for _ in iterator:
        # Simulate actual tournament outcome
        actual = simulate_once_flat(bracket, rng)

        # Score our bracket
        my_score = score_bracket(picks, actual, rp, upset_mode, upset_values)
        total_score += my_score

        # Simulate opponents
        max_opp_score = 0
        for _ in range(pool_size - 1):
            opp = generate_opponent_bracket(bracket, pick_pcts, rng)
            opp_score = score_bracket(opp, actual, rp, upset_mode, upset_values)
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


def _conditional_advance_prob(team: Team,
                              round_num: int,
                              reach_probs: dict[str, dict[int, float]]) -> float:
    """Estimate P(team wins this round | team reached this round)."""
    rounds = reach_probs.get(team.name, {})
    p_current = rounds.get(round_num, 1.0 if round_num == 1 else 0.0)
    p_next = rounds.get(round_num + 1, 0.0)
    if p_current <= 0:
        return 0.0
    return max(0.0, min(1.0, p_next / p_current))


def _build_late_round_slot_candidates(candidates_per_region: list[list[Team]]) -> dict[int, list[Team]]:
    """Build the candidate team list for each late-round slot."""
    return {
        4: candidates_per_region[0],
        5: candidates_per_region[1],
        6: candidates_per_region[2],
        7: candidates_per_region[3],
        2: _unique_teams(candidates_per_region[0] + candidates_per_region[1]),
        3: _unique_teams(candidates_per_region[2] + candidates_per_region[3]),
        1: _unique_teams(
            candidates_per_region[0]
            + candidates_per_region[1]
            + candidates_per_region[2]
            + candidates_per_region[3]
        ),
    }


def _unique_teams(teams: list[Team]) -> list[Team]:
    """Preserve order while removing duplicate teams."""
    seen = set()
    result = []
    for team in teams:
        if team.name in seen:
            continue
        seen.add(team.name)
        result.append(team)
    return result


def _precompute_late_round_scores(sim_results: list[list[Team | None]],
                                  slot_candidates: dict[int, list[Team]],
                                  round_points: dict[int, int],
                                  upset_mode: str | None,
                                  upset_values: dict[int, float] | None) -> dict[int, dict[str, np.ndarray]]:
    """Precompute late-round score arrays for every slot/team candidate pair."""
    score_cache: dict[int, dict[str, np.ndarray]] = {}
    n_sims = len(sim_results)

    for slot, teams in slot_candidates.items():
        score_cache[slot] = {}
        for team in teams:
            scores = np.zeros(n_sims, dtype=float)
            for sim_idx, actual in enumerate(sim_results):
                scores[sim_idx] = _score_single_pick(
                    team, slot, actual, round_points, upset_mode, upset_values
                )
            score_cache[slot][team.name] = scores

    return score_cache


def _precompute_opponent_late_round_scores(bracket: Bracket,
                                           pick_pcts: dict[str, dict[int, float]],
                                           pool_size: int,
                                           sim_results: list[list[Team | None]],
                                           rng: np.random.Generator,
                                           round_points: dict[int, int],
                                           upset_mode: str | None,
                                           upset_values: dict[int, float] | None) -> np.ndarray:
    """Simulate opponents once per tournament sim and keep the best late-round score."""
    opp_max_scores = np.zeros(len(sim_results), dtype=float)
    if pool_size <= 1:
        return opp_max_scores

    for sim_idx, actual in enumerate(sim_results):
        max_score = 0.0
        for _ in range(pool_size - 1):
            opp = generate_opponent_bracket(bracket, pick_pcts, rng)
            opp_score = _score_late_rounds(opp.slots, actual, round_points, upset_mode, upset_values)
            max_score = max(max_score, opp_score)
        opp_max_scores[sim_idx] = max_score

    return opp_max_scores


def _evaluate_late_rounds_from_sims(f4_teams: list[Team],
                                    semi1_winner: Team,
                                    semi2_winner: Team,
                                    champion: Team,
                                    score_cache: dict[int, dict[str, np.ndarray]],
                                    opp_max_scores: np.ndarray,
                                    pool_size: int) -> tuple[float, float]:
    """Estimate late-round pool edge directly from simulated tournament outcomes."""
    our_scores = np.add.reduce((
        score_cache[4][f4_teams[0].name],
        score_cache[5][f4_teams[1].name],
        score_cache[6][f4_teams[2].name],
        score_cache[7][f4_teams[3].name],
        score_cache[2][semi1_winner.name],
        score_cache[3][semi2_winner.name],
        score_cache[1][champion.name],
    ))
    late_win_rate = 1.0 if pool_size <= 1 else float(np.mean(our_scores > opp_max_scores))
    avg_late_score = float(np.mean(our_scores))
    return late_win_rate, avg_late_score


def _score_late_rounds(pick_slots: list[Team | None],
                       actual: list[Team | None],
                       round_points: dict[int, int],
                       upset_mode: str | None,
                       upset_values: dict[int, float] | None) -> float:
    """Score only the Elite Eight, Final Four, and Championship slots."""
    total = 0.0
    for slot in LATE_ROUND_SLOTS:
        total += _score_single_pick(
            pick_slots[slot], slot, actual, round_points, upset_mode, upset_values
        )
    return total


def _score_single_pick(picked_team: Team | None,
                       game_slot: int,
                       actual: list[Team | None],
                       round_points: dict[int, int],
                       upset_mode: str | None,
                       upset_values: dict[int, float] | None) -> float:
    """Score one picked slot against a simulated tournament result."""
    actual_winner = actual[game_slot]
    if picked_team is None or actual_winner is None or picked_team != actual_winner:
        return 0.0

    round_num = LATE_ROUND_NUMBERS[game_slot]
    left_slot = 2 * game_slot
    right_slot = left_slot + 1
    left_team = actual[left_slot] if left_slot < len(actual) else None
    right_team = actual[right_slot] if right_slot < len(actual) else None

    if left_team and right_team:
        loser = right_team if actual_winner == left_team else left_team
        return compute_game_points(
            actual_winner, loser, round_num, round_points, upset_mode, upset_values
        )

    return round_points[round_num]
