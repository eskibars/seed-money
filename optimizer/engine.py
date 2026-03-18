"""Core bracket optimization engine.

Uses a two-phase approach:
1. Exhaustive search over late-round picks (Final Four + Championship)
2. Greedy forward fill for earlier rounds

The goal is to maximize the probability of winning a small family pool,
not just maximizing expected points.
"""

from itertools import product
import math

import numpy as np
from tqdm import tqdm

import config
from models.bracket import Bracket
from models.probability import log5
from models.team import Team
from optimizer.pick_utils import get_matchup_pick_prob, get_round_pick_pct
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
    _print(f"  Pre-simulating {pre_sims} tournaments for evaluation (pass 1)...")
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
    all_scored: list[tuple[tuple, tuple]] = []  # (score, config)
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
                    combo = (champ, list(f4), [semi1_winner, semi2_winner])
                    all_scored.append((score, combo))

    _print(f"  Evaluated {total_combos} late-round combinations")

    # Two-pass refinement: re-evaluate top candidates with more simulations
    # to reduce noise in win rate estimates from the first pass.
    all_scored.sort(key=lambda x: x[0], reverse=True)
    refine_n = min(50, len(all_scored))
    refine_sims = min(5000, n_sims)
    if refine_n > 1 and refine_sims > pre_sims:
        _print(f"  Refining top {refine_n} candidates with {refine_sims} simulations (pass 2)...")
        sim_results_2 = [simulate_once_flat(bracket, rng) for _ in range(refine_sims)]
        score_cache_2 = _precompute_late_round_scores(
            sim_results_2, slot_candidates, rp, upset_mode, upset_values
        )
        opp_max_scores_2 = _precompute_opponent_late_round_scores(
            bracket, pick_pcts, pool_size, sim_results_2, rng, rp, upset_mode, upset_values
        )
        refined: list[tuple[tuple, tuple]] = []
        for orig_score, cfg in all_scored[:refine_n]:
            champ_r, f4_r, semis_r = cfg
            late_win_rate_2, avg_late_score_2 = _evaluate_late_rounds_from_sims(
                f4_r, semis_r[0], semis_r[1], champ_r,
                score_cache_2, opp_max_scores_2, pool_size
            )
            # Reuse the heuristic score from pass 1 (deterministic)
            refined_score = (
                late_win_rate_2,
                accuracy_weight * avg_late_score_2 + (1 - accuracy_weight) * orig_score[2],
                orig_score[2],
            )
            refined.append((refined_score, cfg))
        refined.sort(key=lambda x: x[0], reverse=True)
        best_score, best_config = refined[0]
    else:
        best_score = all_scored[0][0] if all_scored else None
        best_config = all_scored[0][1] if all_scored else None

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
    """Quick scoring of a late-round configuration using leverage-aware value.

    No simulation needed — uses pre-computed reach probabilities.
    Now accounts for upset bonuses in semifinal and championship matchups.
    """
    rp = round_points or config.ROUND_POINTS
    total_emv = 0.0

    # Score Final Four picks by the unconditional chance of reaching the F4.
    # We do not know the exact Elite Eight opponent yet at this stage.
    for team in f4_teams:
        p_reach = reach_probs.get(team.name, {}).get(5, 0.0)
        pick_frac = get_round_pick_pct(pick_pcts, team.name, team.seed, 5)
        emv = _compute_pick_value(p_reach, pick_frac, rp[4], pool_size, accuracy_weight)
        total_emv += emv

    # Score semifinal winners (round 5 = Final Four)
    # Use Log5 for the specific matchup instead of marginal reach probabilities,
    # then weight by the probability both teams actually reached the Final Four.
    for winner, loser in [(semi1_winner, semi1_loser), (semi2_winner, semi2_loser)]:
        p_winner_f4 = reach_probs.get(winner.name, {}).get(5, 0.0)
        p_loser_f4 = reach_probs.get(loser.name, {}).get(5, 0.0)
        p_matchup_win = log5(winner.rating, loser.rating)
        # P(winner wins this semifinal) ≈ P(both reach F4) × P(winner beats loser)
        p_reach = p_winner_f4 * p_loser_f4 * p_matchup_win
        pick_frac = get_matchup_pick_prob(
            pick_pcts, winner.name, winner.seed, loser.name, loser.seed, 6
        )
        pts = compute_game_points(winner, loser, 5, rp, upset_mode, upset_values)
        emv = _compute_pick_value(p_reach, pick_frac, pts, pool_size, accuracy_weight)
        total_emv += emv

    # Score champion (round 6 = Championship)
    # Use Log5 for the specific championship matchup.
    p_semi1_win = reach_probs.get(semi1_winner.name, {}).get(5, 0.0) * \
                  reach_probs.get(semi1_loser.name, {}).get(5, 0.0) * \
                  log5(semi1_winner.rating, semi1_loser.rating)
    p_semi2_win = reach_probs.get(semi2_winner.name, {}).get(5, 0.0) * \
                  reach_probs.get(semi2_loser.name, {}).get(5, 0.0) * \
                  log5(semi2_winner.rating, semi2_loser.rating)
    p_champ = p_semi1_win * p_semi2_win * log5(champion.rating, champ_loser.rating)
    champ_pick = get_matchup_pick_prob(
        pick_pcts, champion.name, champion.seed, champ_loser.name, champ_loser.seed, 7
    )
    champ_pts = compute_game_points(champion, champ_loser, 6, rp, upset_mode, upset_values)
    emv = _compute_pick_value(p_champ, champ_pick, champ_pts, pool_size, accuracy_weight)
    total_emv += emv

    return total_emv


def _compute_pick_value(model_prob: float, public_prob: float, points: float,
                        pool_size: int, accuracy_weight: float) -> float:
    """Score a pick by blending accuracy with game-level leverage.

    At ``accuracy_weight=1`` this reduces to expected points. As the slider
    moves toward contrarian, picks get rewarded for beating the public's
    implied probability, with a stronger effect in larger pools.
    """
    if model_prob <= 0:
        return 0.0

    if accuracy_weight >= 1.0:
        return model_prob * points

    public_prob = min(0.999, max(0.001, public_prob))
    leverage_ratio = model_prob / public_prob
    leverage_ratio = min(4.0, max(0.25, leverage_ratio))

    contrarian_weight = max(0.0, 1.0 - accuracy_weight)
    leverage_exponent = contrarian_weight * max(1.0, math.log(max(pool_size, 2)))
    leverage_multiplier = leverage_ratio ** leverage_exponent

    return model_prob * points * leverage_multiplier


def _fill_bracket_forward(result, champion, f4_teams, semi_winners,
                          reach_probs, pick_pcts, pool_size, accuracy_weight,
                          round_points=None,
                          upset_mode=None, upset_values=None):
    """Fill each region subtree optimally, conditioned on the chosen F4 teams."""
    rp = round_points or config.ROUND_POINTS

    # Set the late-round results
    result.set_winner(1, champion)
    result.set_winner(2, semi_winners[0])
    result.set_winner(3, semi_winners[1])
    for i, team in enumerate(f4_teams):
        result.set_winner(4 + i, team)

    # Optimize each regional subtree jointly so coordinated upset paths can win.
    for region_idx, forced_team in enumerate(f4_teams):
        root_slot = 4 + region_idx
        plans = _optimize_region_subtree(
            result,
            root_slot,
            reach_probs,
            pick_pcts,
            pool_size,
            accuracy_weight,
            rp,
            upset_mode,
            upset_values,
        )
        chosen = plans.get(forced_team)
        if chosen is None:
            raise RuntimeError(f"Unable to build a bracket path for forced team {forced_team.name}")
        _, picks = chosen
        for slot, winner in picks.items():
            result.set_winner(slot, winner)

    return result


def _optimize_region_subtree(bracket: Bracket,
                             game_slot: int,
                             reach_probs: dict[str, dict[int, float]],
                             pick_pcts: dict[str, dict[int, float]],
                             pool_size: int,
                             accuracy_weight: float,
                             round_points: dict[int, int],
                             upset_mode: str | None,
                             upset_values: dict[int, float] | None) -> dict[Team, tuple[float, dict[int, Team]]]:
    """Return the best subtree plan for each possible winner at a game slot."""
    if game_slot >= 64:
        team = bracket.slots[game_slot]
        return {team: (0.0, {})} if team is not None else {}

    left_slot, right_slot = bracket.get_matchup(game_slot)
    left_plans = _optimize_region_subtree(
        bracket, left_slot, reach_probs, pick_pcts, pool_size, accuracy_weight,
        round_points, upset_mode, upset_values
    )
    right_plans = _optimize_region_subtree(
        bracket, right_slot, reach_probs, pick_pcts, pool_size, accuracy_weight,
        round_points, upset_mode, upset_values
    )

    round_num = bracket.get_round(game_slot)
    plans: dict[Team, tuple[float, dict[int, Team]]] = {}

    for team_a, (score_a, picks_a) in left_plans.items():
        for team_b, (score_b, picks_b) in right_plans.items():
            if team_a is None or team_b is None:
                continue

            shared_picks = dict(picks_a)
            shared_picks.update(picks_b)

            for winner, loser in ((team_a, team_b), (team_b, team_a)):
                # Use unconditional reach probability P(team wins this game)
                # = P(team reaches next round), NOT the conditional
                # P(win | reached) which inflates deep upset path values.
                model_prob = reach_probs.get(winner.name, {}).get(round_num + 1, 0.0)
                pick_value = _compute_pick_value(
                    model_prob,
                    get_matchup_pick_prob(
                        pick_pcts,
                        winner.name,
                        winner.seed,
                        loser.name,
                        loser.seed,
                        round_num + 1,
                    ),
                    compute_game_points(winner, loser, round_num, round_points, upset_mode, upset_values),
                    pool_size,
                    accuracy_weight,
                )
                total_score = score_a + score_b + pick_value
                existing = plans.get(winner)
                if existing is not None and existing[0] >= total_score:
                    continue

                picks = dict(shared_picks)
                picks[game_slot] = winner
                plans[winner] = (total_score, picks)

    return plans


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
        elif my_score == max_opp_score:
            # Split ties: in real pools ties are broken by tiebreaker,
            # model as 50/50 to avoid systematic pessimism.
            wins += 0.5

    return wins / n_sims, total_score / n_sims


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
    if pool_size <= 1:
        late_win_rate = 1.0
    else:
        # Count outright wins + half credit for ties (tiebreaker modeled as 50/50)
        late_win_rate = float(np.mean(
            (our_scores > opp_max_scores).astype(float)
            + 0.5 * (our_scores == opp_max_scores).astype(float)
        ))
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
