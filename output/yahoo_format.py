"""Yahoo bracket fill-in format.

Outputs picks in the order Yahoo presents them, making it easy to
fill in a Yahoo bracket by going left-to-right, round-by-round.
"""

from models.bracket import Bracket


def print_yahoo_format(bracket: Bracket):
    """Print picks in Yahoo fill-in order.

    Yahoo's bracket is presented region by region, round by round:
    - All Round of 64 games for each region
    - Then Round of 32
    - Then Sweet 16
    - Then Elite 8
    - Then Final Four + Championship
    """
    print("\n" + "=" * 60)
    print("    YAHOO BRACKET FILL-IN ORDER")
    print("    (Copy these picks into Yahoo, top to bottom)")
    print("=" * 60)

    pick_num = 0

    # Rounds 1-4: within each region
    for region_idx in range(4):
        region_name = bracket.regions.get(region_idx, f"Region {region_idx + 1}")
        print(f"\n--- {region_name.upper()} ---")

        # Round of 64
        game_base = 32 + region_idx * 8
        print("  Round of 64:")
        for i in range(8):
            game_slot = game_base + i
            winner = bracket.slots[game_slot]
            if winner:
                pick_num += 1
                print(f"    {pick_num:2d}. {winner}")

        # Round of 32
        r32_base = 16 + region_idx * 4
        print("  Round of 32:")
        for i in range(4):
            game_slot = r32_base + i
            winner = bracket.slots[game_slot]
            if winner:
                pick_num += 1
                print(f"    {pick_num:2d}. {winner}")

        # Sweet 16
        s16_base = 8 + region_idx * 2
        print("  Sweet 16:")
        for i in range(2):
            game_slot = s16_base + i
            winner = bracket.slots[game_slot]
            if winner:
                pick_num += 1
                print(f"    {pick_num:2d}. {winner}")

        # Elite 8
        e8_slot = 4 + region_idx
        winner = bracket.slots[e8_slot]
        if winner:
            pick_num += 1
            print(f"  Elite 8:")
            print(f"    {pick_num:2d}. {winner}")

    # Final Four + Championship
    print(f"\n--- FINAL FOUR ---")

    # Semifinal 1
    sf1 = bracket.slots[2]
    if sf1:
        pick_num += 1
        print(f"  Semifinal 1:")
        print(f"    {pick_num:2d}. {sf1}")

    # Semifinal 2
    sf2 = bracket.slots[3]
    if sf2:
        pick_num += 1
        print(f"  Semifinal 2:")
        print(f"    {pick_num:2d}. {sf2}")

    # Championship
    champ = bracket.slots[1]
    if champ:
        pick_num += 1
        print(f"\n--- CHAMPIONSHIP ---")
        print(f"    {pick_num:2d}. {champ}")

    print(f"\n  Total picks: {pick_num}")
    print("=" * 60)


def export_picks_csv(bracket: Bracket, filepath: str):
    """Export picks as a CSV file.

    Columns: pick_number, round, region, seed, team_name
    """
    import csv

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pick_number", "round", "region", "seed", "team"])

        pick_num = 0

        for round_num in range(1, 7):
            for game_slot in bracket.get_all_game_slots_for_round(round_num):
                winner = bracket.slots[game_slot]
                if winner:
                    pick_num += 1
                    region_idx = bracket.get_region_index(game_slot)
                    region = bracket.regions.get(region_idx, "Final Four") if region_idx is not None else "Final Four"
                    writer.writerow([pick_num, round_num, region, winner.seed, winner.name])

    print(f"Exported {pick_num} picks to {filepath}")
