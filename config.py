"""Central configuration for the March Madness bracket optimizer."""

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
