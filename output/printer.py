"""Pretty-print bracket output."""

from tabulate import tabulate

import config
from models.bracket import Bracket
from models.team import Team


def print_bracket(bracket: Bracket, reach_probs: dict[str, dict[int, float]] | None = None):
    """Print the full bracket in a readable format.

    Args:
        bracket: A filled bracket
        reach_probs: Optional probability data to show alongside picks
    """
    print("\n" + "=" * 60)
    print("           OPTIMIZED BRACKET")
    print("=" * 60)

    # Print each region
    for region_idx in range(4):
        region_name = bracket.regions.get(region_idx, f"Region {region_idx + 1}")
        print(f"\n--- {region_name.upper()} REGION ---")

        # Round of 64 (round 1)
        base = 64 + region_idx * 16
        game_base = 32 + region_idx * 8

        print(f"\n  Round of 64:")
        for i in range(8):
            game_slot = game_base + i
            left = bracket.slots[base + 2 * i]
            right = bracket.slots[base + 2 * i + 1]
            winner = bracket.slots[game_slot]
            if left and right and winner:
                print(f"    {left} vs {right}  ->  {winner}")

        # Round of 32 (round 2)
        r32_base = 16 + region_idx * 4
        print(f"  Round of 32:")
        for i in range(4):
            game_slot = r32_base + i
            winner = bracket.slots[game_slot]
            left_game = game_slot * 2
            right_game = game_slot * 2 + 1
            team_a = bracket.slots[left_game]
            team_b = bracket.slots[right_game]
            if team_a and team_b and winner:
                print(f"    {team_a} vs {team_b}  ->  {winner}")

        # Sweet 16 (round 3)
        s16_base = 8 + region_idx * 2
        print(f"  Sweet 16:")
        for i in range(2):
            game_slot = s16_base + i
            winner = bracket.slots[game_slot]
            team_a = bracket.slots[game_slot * 2]
            team_b = bracket.slots[game_slot * 2 + 1]
            if team_a and team_b and winner:
                print(f"    {team_a} vs {team_b}  ->  {winner}")

        # Elite 8 (round 4) = regional final
        e8_slot = 4 + region_idx
        e8_winner = bracket.slots[e8_slot]
        team_a = bracket.slots[e8_slot * 2]
        team_b = bracket.slots[e8_slot * 2 + 1]
        if team_a and team_b and e8_winner:
            print(f"  Elite Eight:")
            print(f"    {team_a} vs {team_b}  ->  {e8_winner}")

    # Final Four
    print(f"\n{'=' * 60}")
    print("           FINAL FOUR")
    print("=" * 60)

    # Semifinal 1 (slot 2): region 0 winner vs region 1 winner
    sf1_a = bracket.slots[4]
    sf1_b = bracket.slots[5]
    sf1_winner = bracket.slots[2]
    if sf1_a and sf1_b and sf1_winner:
        print(f"\n  Semifinal 1: {sf1_a} vs {sf1_b}  ->  {sf1_winner}")

    # Semifinal 2 (slot 3): region 2 winner vs region 3 winner
    sf2_a = bracket.slots[6]
    sf2_b = bracket.slots[7]
    sf2_winner = bracket.slots[3]
    if sf2_a and sf2_b and sf2_winner:
        print(f"  Semifinal 2: {sf2_a} vs {sf2_b}  ->  {sf2_winner}")

    # Championship (slot 1)
    champ_a = bracket.slots[2]
    champ_b = bracket.slots[3]
    champion = bracket.slots[1]
    if champ_a and champ_b and champion:
        print(f"\n  CHAMPIONSHIP: {champ_a} vs {champ_b}")
        print(f"  CHAMPION: {champion}")

    print("\n" + "=" * 60)

    # Summary stats
    if reach_probs and champion:
        p_champ = reach_probs.get(champion.name, {}).get(7, 0)
        print(f"  Champion win probability: {p_champ:.1%}")


def print_summary_table(bracket: Bracket, reach_probs: dict[str, dict[int, float]],
                        pick_pcts: dict[str, dict[int, float]] | None = None):
    """Print a summary table of key picks with probabilities and leverage."""
    print("\n=== KEY PICKS SUMMARY ===\n")

    rows = []

    has_pick_data = bool(pick_pcts)

    # Champion
    champion = bracket.slots[1]
    if champion:
        p = reach_probs.get(champion.name, {}).get(7, 0)
        pp = (pick_pcts or {}).get(champion.name, {}).get(7, 0)
        pp_str = f"{pp:.1%}" if has_pick_data else "N/A"
        leverage_str = f"{p / pp:.1f}x" if pp > 0 else ("N/A" if not has_pick_data else "unique")
        rows.append(["Champion", str(champion), f"{p:.1%}", pp_str, leverage_str])

    # Final Four
    for slot in [4, 5, 6, 7]:
        team = bracket.slots[slot]
        if team:
            p = reach_probs.get(team.name, {}).get(5, 0)
            pp = (pick_pcts or {}).get(team.name, {}).get(5, 0)
            pp_str = f"{pp:.1%}" if has_pick_data else "N/A"
            leverage_str = f"{p / pp:.1f}x" if pp > 0 else ("N/A" if not has_pick_data else "unique")
            region = bracket.regions.get(slot - 4, "?")
            rows.append([f"F4 ({region})", str(team), f"{p:.1%}", pp_str, leverage_str])

    headers = ["Pick", "Team", "P(reach)", "Public %", "Leverage"]
    print(tabulate(rows, headers=headers, tablefmt="simple"))
