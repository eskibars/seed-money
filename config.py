"""Central configuration for the March Madness bracket optimizer."""

import os

# Scoring: points awarded per correct pick in each round
# Round 1 = Round of 64, Round 6 = Championship
ROUND_POINTS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 5}

# Number of games per round
GAMES_PER_ROUND = {1: 32, 2: 16, 3: 8, 4: 4, 5: 2, 6: 1}

# Max possible score: 32*1 + 16*2 + 8*3 + 4*4 + 2*4 + 1*5 = 117
MAX_SCORE = sum(GAMES_PER_ROUND[r] * ROUND_POINTS[r] for r in range(1, 7))

# Pool settings
DEFAULT_POOL_SIZE = 7

# Optimizer settings
DEFAULT_SIMULATIONS = 10_000
DEFAULT_ACCURACY_WEIGHT = 0.75  # 0=full contrarian, 1=full accuracy
DEFAULT_SIMULATION_SOURCE = "consensus"

RATING_SOURCES = {
    "consensus": {
        "label": "Consensus Blend",
        "web": True,
        "refresh_default": False,
    },
    "torvik": {
        "label": "Bart Torvik",
        "web": True,
        "refresh_default": True,
    },
    "kenpom": {
        "label": "KenPom",
        "web": True,
        "refresh_default": True,
    },
    "espn": {
        "label": "ESPN BPI",
        "web": False,
        "refresh_default": True,
    },
    "paine": {
        "label": "Neil Paine",
        "web": True,
        "refresh_default": True,
    },
    "manual": {
        "label": "Manual CSV",
        "web": False,
        "refresh_default": False,
    },
}
DEFAULT_REFRESH_RATING_SOURCES = tuple(
    source
    for source, meta in RATING_SOURCES.items()
    if meta.get("refresh_default")
)

RATING_SOURCE_WEIGHTS = {
    "torvik": 0.45,
    "kenpom": 0.30,
    "espn": 0.15,
    "paine": 0.10,
}

# Scoring presets for common pool formats
SCORING_PRESETS = {
    "family": {1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5},
    "standard": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6},
    "espn": {1: 10, 2: 20, 3: 40, 4: 80, 5: 160, 6: 320},
}
F4_CANDIDATES_PER_REGION = 8   # Top N teams per region to consider for Final Four

# Bracket structure
NUM_TEAMS = 64
NUM_GAMES = 63
NUM_REGIONS = 4
REGION_NAMES = ["East", "West", "South", "Midwest"]

# Standard seed matchups in round 1 (within each region)
SEED_MATCHUPS = [
    (1, 16), (8, 9), (5, 12), (4, 13),
    (6, 11), (3, 14), (7, 10), (2, 15),
]

# Public pick-source blending. Weights are normalized over sources that have
# an entry for a given team/round, so partial backfills stay safe to blend.
PICK_SOURCE_WEIGHTS = {
    "espn": 0.60,
    "yahoo": 0.40,
    "ncaa": 0.15,
    "cbs": 0.10,
}

# ESPN Tournament Challenge public-pick ingestion.
ESPN_PICKS_PROPOSITIONS_URL = "https://gambit-api.fantasy.espn.com/apis/v1/propositions"
ESPN_PICKS_SCORING_PERIODS = (1, 2, 3, 4, 5, 6)
ESPN_TOURNAMENT_CHALLENGE_IDS = {
    2026: 277,
}

# Optional article URLs for partial pick backfills.
# Rounds use the optimizer's "reach round N" convention:
# 2 = win first game, 5 = reach Final Four, 7 = win championship.
NCAA_PICK_ARTICLE_URLS = {
    7: os.environ.get("SEED_MONEY_NCAA_CHAMPION_URL", ""),
    5: os.environ.get("SEED_MONEY_NCAA_FINAL_FOUR_URL", ""),
    2: os.environ.get("SEED_MONEY_NCAA_UPSET_URL", ""),
}
CBS_PICK_ARTICLE_URLS = {
    7: os.environ.get("SEED_MONEY_CBS_CHAMPION_URL", ""),
    5: os.environ.get("SEED_MONEY_CBS_FINAL_FOUR_URL", ""),
    2: os.environ.get("SEED_MONEY_CBS_UPSET_URL", ""),
}
