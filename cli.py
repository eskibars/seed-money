"""March Madness Bracket Optimizer - CLI entry point.

Usage:
    python cli.py fetch-ratings [--source torvik|espn] [--year 2026]
    python cli.py load-bracket [--interactive | --file path.json]
    python cli.py fetch-picks [--source espn|yahoo | --manual path.csv]
    python cli.py simulate [--sims 10000]
    python cli.py optimize [--pool-size 7] [--accuracy-weight 0.75] [--force-champion "Duke"]
    python cli.py show
    python cli.py export [--format yahoo|csv] [--output path]
"""

import argparse
import json
import os
import pickle
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_FILE = os.path.join(DATA_DIR, "state.pkl")


def save_state(state: dict):
    """Save intermediate state to disk."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "wb") as f:
        pickle.dump(state, f)


def load_state() -> dict:
    """Load intermediate state from disk."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "rb") as f:
            return pickle.load(f)
    return {}


# --- Commands ---

def cmd_fetch_ratings(args):
    """Fetch team power ratings."""
    state = load_state()

    if args.source == "torvik":
        from ingestion.torvik import fetch_torvik_ratings, parse_torvik_ratings
        df = fetch_torvik_ratings(year=args.year)
        ratings = parse_torvik_ratings(df)
    elif args.source == "espn":
        from ingestion.espn_bpi import fetch_espn_bpi, bpi_to_rating
        raw = fetch_espn_bpi()
        ratings = {}
        for name, data in raw.items():
            ratings[name] = {
                "rating": bpi_to_rating(data["bpi"]),
                "adj_offense": 100.0,
                "adj_defense": 100.0,
            }
    elif args.source == "manual":
        from ingestion.manual_entry import load_ratings_from_csv
        ratings = load_ratings_from_csv(args.file)
    else:
        print(f"Unknown source: {args.source}")
        return

    state["ratings"] = ratings
    save_state(state)

    # Print top teams
    sorted_teams = sorted(ratings.items(), key=lambda x: x[1].get("rating", 0), reverse=True)
    print(f"\nLoaded ratings for {len(ratings)} teams.")
    print("\nTop 20 teams by rating:")
    for i, (name, data) in enumerate(sorted_teams[:20], 1):
        r = data.get("rating", 0)
        print(f"  {i:2d}. {name:<25s} {r:.4f}")


def cmd_load_bracket(args):
    """Load the 64-team bracket."""
    state = load_state()
    ratings = state.get("ratings")

    if args.file:
        from ingestion.bracket_loader import load_bracket_from_json, save_bracket_to_json
        bracket = load_bracket_from_json(args.file, ratings)
    else:
        from ingestion.bracket_loader import load_bracket_interactive, save_bracket_to_json
        bracket = load_bracket_interactive(ratings)
        # Save for future use
        save_path = os.path.join(RAW_DIR, "bracket.json")
        save_bracket_to_json(bracket, save_path)

    state["bracket"] = bracket
    save_state(state)
    print(f"\nBracket loaded: {len(bracket.teams)} teams in {len(bracket.regions)} regions")


def cmd_fetch_picks(args):
    """Fetch public pick percentages."""
    state = load_state()

    if args.manual:
        from ingestion.manual_entry import load_pick_pcts_from_csv
        pick_pcts = load_pick_pcts_from_csv(args.manual)
    elif args.source == "espn":
        from ingestion.pick_popularity import fetch_espn_picks
        pick_pcts = fetch_espn_picks()
    elif args.source == "yahoo":
        from ingestion.pick_popularity import fetch_yahoo_picks
        pick_pcts = fetch_yahoo_picks()
    else:
        print(f"Unknown source: {args.source}")
        return

    if not pick_pcts:
        print("\nNo pick data retrieved. Pick data is only available during the tournament.")
        print("You can still run 'optimize' with --no-picks to use probability-only mode.")
        return

    state["pick_pcts"] = pick_pcts
    save_state(state)
    print(f"\nLoaded pick percentages for {len(pick_pcts)} teams")


def cmd_simulate(args):
    """Run Monte Carlo tournament simulation."""
    state = load_state()

    bracket = state.get("bracket")
    if not bracket:
        print("ERROR: No bracket loaded. Run 'python cli.py load-bracket' first.")
        return

    from optimizer.simulator import simulate_tournament
    reach_probs = simulate_tournament(bracket, n_sims=args.sims, seed=42)

    state["reach_probs"] = reach_probs
    save_state(state)

    # Print top championship probabilities
    champ_probs = [(name, probs.get(7, 0)) for name, probs in reach_probs.items()]
    champ_probs.sort(key=lambda x: x[1], reverse=True)

    print(f"\nTop 15 championship probabilities:")
    for name, p in champ_probs[:15]:
        print(f"  {name:<25s} {p:.1%}")


def cmd_optimize(args):
    """Run the bracket optimizer."""
    state = load_state()

    bracket = state.get("bracket")
    if not bracket:
        print("ERROR: No bracket loaded. Run 'python cli.py load-bracket' first.")
        return

    # Run simulation if not already done
    reach_probs = state.get("reach_probs")
    if not reach_probs:
        print("Running tournament simulation first...")
        from optimizer.simulator import simulate_tournament
        reach_probs = simulate_tournament(bracket, n_sims=args.sims, seed=42)
        state["reach_probs"] = reach_probs

    pick_pcts = state.get("pick_pcts", {})
    if not pick_pcts and not args.no_picks:
        print("WARNING: No pick percentage data loaded.")
        print("Running in probability-only mode (no contrarian component).")
        print("To include public pick data, run 'python cli.py fetch-picks' first.\n")

    from optimizer.engine import optimize
    optimized = optimize(
        bracket=bracket,
        reach_probs=reach_probs,
        pick_pcts=pick_pcts,
        pool_size=args.pool_size,
        accuracy_weight=args.accuracy_weight,
        n_sims=args.sims,
        force_champion=args.force_champion,
    )

    state["optimized"] = optimized
    save_state(state)

    # Auto-show the bracket
    from output.printer import print_bracket, print_summary_table
    print_bracket(optimized, reach_probs)
    print_summary_table(optimized, reach_probs, pick_pcts)


def cmd_show(args):
    """Display the optimized bracket."""
    state = load_state()

    optimized = state.get("optimized")
    if not optimized:
        print("ERROR: No optimized bracket. Run 'python cli.py optimize' first.")
        return

    reach_probs = state.get("reach_probs", {})
    pick_pcts = state.get("pick_pcts", {})

    from output.printer import print_bracket, print_summary_table
    print_bracket(optimized, reach_probs)
    print_summary_table(optimized, reach_probs, pick_pcts)


def cmd_export(args):
    """Export the optimized bracket."""
    state = load_state()

    optimized = state.get("optimized")
    if not optimized:
        print("ERROR: No optimized bracket. Run 'python cli.py optimize' first.")
        return

    if args.format == "yahoo":
        from output.yahoo_format import print_yahoo_format
        print_yahoo_format(optimized)
    elif args.format == "csv":
        from output.yahoo_format import export_picks_csv
        output_path = args.output or os.path.join(DATA_DIR, "bracket_picks.csv")
        export_picks_csv(optimized, output_path)
    else:
        print(f"Unknown format: {args.format}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="March Madness Bracket Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workflow:
  1. python cli.py fetch-ratings                    # Download team ratings (available now)
  2. python cli.py load-bracket --file bracket.json  # Enter bracket (after Selection Sunday)
  3. python cli.py fetch-picks --source espn         # Get pick %s (after brackets open)
  4. python cli.py optimize --pool-size 7            # Run optimizer
  5. python cli.py export --format yahoo             # Get Yahoo fill-in order
        """
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # fetch-ratings
    p_ratings = subparsers.add_parser("fetch-ratings", help="Fetch team power ratings")
    p_ratings.add_argument("--source", choices=["torvik", "espn", "manual"], default="torvik")
    p_ratings.add_argument("--year", type=int, default=2026)
    p_ratings.add_argument("--file", help="CSV file path (for --source manual)")

    # load-bracket
    p_bracket = subparsers.add_parser("load-bracket", help="Load the 64-team bracket")
    p_bracket.add_argument("--file", help="JSON file with bracket data")
    p_bracket.add_argument("--interactive", action="store_true", help="Enter bracket interactively")

    # fetch-picks
    p_picks = subparsers.add_parser("fetch-picks", help="Fetch public pick percentages")
    p_picks.add_argument("--source", choices=["espn", "yahoo"], default="espn")
    p_picks.add_argument("--manual", help="CSV file with pick percentages")

    # simulate
    p_sim = subparsers.add_parser("simulate", help="Run Monte Carlo tournament simulation")
    p_sim.add_argument("--sims", type=int, default=config.DEFAULT_SIMULATIONS)

    # optimize
    p_opt = subparsers.add_parser("optimize", help="Run bracket optimizer")
    p_opt.add_argument("--pool-size", type=int, default=config.DEFAULT_POOL_SIZE)
    p_opt.add_argument("--accuracy-weight", type=float, default=config.DEFAULT_ACCURACY_WEIGHT)
    p_opt.add_argument("--sims", type=int, default=config.DEFAULT_SIMULATIONS)
    p_opt.add_argument("--force-champion", help="Force a specific team as champion")
    p_opt.add_argument("--no-picks", action="store_true", help="Run without pick popularity data")

    # show
    subparsers.add_parser("show", help="Display the optimized bracket")

    # export
    p_export = subparsers.add_parser("export", help="Export the optimized bracket")
    p_export.add_argument("--format", choices=["yahoo", "csv"], default="yahoo")
    p_export.add_argument("--output", help="Output file path (for csv format)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "fetch-ratings": cmd_fetch_ratings,
        "load-bracket": cmd_load_bracket,
        "fetch-picks": cmd_fetch_picks,
        "simulate": cmd_simulate,
        "optimize": cmd_optimize,
        "show": cmd_show,
        "export": cmd_export,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
