# Seed Money — March Madness Bracket Optimizer

A bracket optimization engine that maximizes your probability of **winning** a March Madness pool, not just maximizing expected points. It exploits the gap between a team's true probability of advancing and how often the public picks them — what we call **point leverage**.

## Why Point Leverage Matters

Most bracket advice tells you to "pick the best teams." That's wrong for pools.

In a pool, you don't win by being accurate — you win by being accurate *where your opponents are wrong*. Picking Duke to win it all might be the most likely outcome, but if 40% of your pool also picks Duke, a correct Duke pick barely separates you from the field.

**Point leverage** is the ratio of a team's model probability to their public pick rate:

```
leverage = P(team advances) / P(public picks them)
```

- **Leverage > 1**: The public is sleeping on this team. A correct pick here gains you ground against most opponents.
- **Leverage < 1**: The public overvalues this team. Even if they win, many opponents also get the points.
- **Leverage = 1**: No edge — the public has priced this team correctly.

The optimizer blends accuracy with leverage based on your pool size and strategy preference. In a 7-person family pool, mild contrarianism is enough. In a 500-person office pool, you need bigger swings to stand out.

## How It Works

### Two-Phase Optimization

**Phase 1 — Late-Round Exhaustive Search**

The Final Four, semifinals, and championship are where pools are won or lost. These picks carry the most points and the most differentiation.

1. Identifies the top 8 candidates per region by reach probability
2. Pre-simulates 1,000–5,000 tournament outcomes
3. Exhaustively evaluates every valid combination of F4 teams × semifinal winners × champion
4. Scores each combination by simulated pool win rate against realistic opponents
5. Refines the top 50 candidates with deeper simulation (5,000 sims)

**Phase 2 — Regional Subtree Fill**

With the late rounds locked in, each region's bracket is filled using bottom-up dynamic programming:

1. Teams that must reach the Final Four are "forced" through their path
2. For every other game, the optimizer evaluates all possible winner combinations through the subtree
3. Each pick is scored by `P(reach) × points × leverage_multiplier`
4. The best coordinated path per region is selected — not just game-by-game greedy picks

**Phase 3 — Monte Carlo Validation**

The final bracket is validated against 10,000 simulated tournaments with realistic opponent pools:
- Opponents are generated using public pick percentages (ESPN + Yahoo consensus)
- Reports pool win rate, expected score, and advantage over random

### Data Sources

| Source | What It Provides |
|--------|-----------------|
| Bart Torvik | Power ratings (Barthag), adjusted offense/defense |
| KenPom | Power ratings, adjusted efficiency |
| ESPN BPI | Basketball Power Index ratings |
| Neil Paine | Ratings + round-by-round advance probabilities |
| DraftKings | Betting-implied win probabilities |
| ESPN Tournament Challenge | Public pick percentages by team and round |
| Yahoo Bracket | Public pick distribution |

Ratings are blended into a consensus (Torvik 45%, KenPom 30%, ESPN 15%, Paine 10%). Public picks are blended by source coverage (ESPN 60%, Yahoo 40%).

### Scoring Systems

Three built-in presets plus fully custom:

| Preset | R64 | R32 | S16 | E8 | F4 | Champ |
|--------|-----|-----|-----|----|----|-------|
| Family Pool | 1 | 2 | 3 | 3 | 4 | 5 |
| Standard | 1 | 2 | 3 | 4 | 5 | 6 |
| ESPN | 10 | 20 | 40 | 80 | 160 | 320 |

**Upset Bonuses** (optional, per-round):
- *Seed Difference Multiplier*: bonus = (winner_seed − loser_seed) × multiplier
- *Fixed Bonus*: flat bonus for any upset, regardless of seed gap

### Strategy Balance Slider

The accuracy weight (0–1) controls how much the optimizer favors likely outcomes vs. contrarian edges:

- **1.0 (Accurate)**: Pure expected value. Picks the most likely winners. Good for tiny pools (2–3 people).
- **0.75 (Default)**: Mild contrarian tilt. Slightly favors undervalued teams. Good for family pools (5–10 people).
- **0.0 (Contrarian)**: Aggressively seeks leverage. Picks upset-heavy brackets to differentiate. Good for large pools (50+).

The effect scales with pool size — in a 7-person pool, even max contrarian is fairly conservative; in a 500-person pool, it takes real swings.

## Architecture

```
march-madness/
├── config.py                 # Scoring presets, rating weights, defaults
├── cli.py                    # Command-line interface
├── models/
│   ├── team.py               # Team dataclass (name, seed, rating, region)
│   ├── bracket.py            # 128-slot binary tree bracket structure
│   └── probability.py        # Log5 win probability model
├── ingestion/
│   ├── ratings_sources.py    # Rating source registry and dispatch
│   ├── bracket_loader.py     # Bracket construction from JSON/interactive
│   ├── bracket_fetcher.py    # Fetch bracket from Yahoo/ESPN
│   ├── torvik.py             # Bart Torvik ratings
│   ├── kenpom.py             # KenPom ratings
│   ├── espn_bpi.py           # ESPN BPI ratings
│   ├── neil_paine.py         # Neil Paine ratings + forecasts
│   ├── draftkings.py         # DraftKings implied odds
│   └── pick_popularity.py    # ESPN/Yahoo public pick percentages
├── optimizer/
│   ├── engine.py             # Two-phase optimizer (late rounds + fill forward)
│   ├── scorer.py             # Bracket scoring with upset bonus support
│   ├── simulator.py          # Monte Carlo tournament simulation
│   ├── pool_model.py         # Opponent bracket generation
│   ├── pick_utils.py         # Public pick consensus and lookup
│   ├── reach_prob_utils.py   # Reach probability resolution
│   └── rating_utils.py       # Multi-source rating consensus
├── output/
│   ├── html_export.py        # Self-contained HTML bracket visualization
│   ├── printer.py            # CLI table output
│   └── yahoo_format.py       # Yahoo bracket export
└── web/
    ├── app.py                # Flask app with job queue
    ├── services.py           # Optimization pipeline orchestration
    ├── database.py           # SQLite schema (ratings, picks, brackets, jobs)
    └── refresh.py            # Background data refresh
```

### Bracket Data Model

The bracket is a 128-element array representing a binary tree:

```
Slot 1:     Championship winner
Slots 2-3:  Semifinal winners
Slots 4-7:  Elite Eight winners (regional champions)
Slots 8-15: Sweet Sixteen winners
Slots 16-31: Round of 32 winners
Slots 32-63: Round of 64 winners
Slots 64-127: Starting 64 teams (leaves)
```

For any game at slot `n`, the two participants come from slots `2n` and `2n+1`. This makes path traversal trivial — a team at starting slot `s` must win games at `s//2`, `s//4`, ..., down to slot 1.

### Win Probability

Game outcomes use the **Log5 model**:

```
P(A beats B) = A(1-B) / [A(1-B) + B(1-A)]
```

where A and B are team ratings on a 0–1 scale. When direct round-by-round forecasts are available (e.g., from Neil Paine), those are used instead — giving more accurate estimates that account for bracket position and strength of schedule.

### Opponent Modeling

Opponents are not random — they follow public pick patterns. `generate_opponent_bracket()` fills a bracket round-by-round using public pick percentages, ensuring bracket coherence (a team can only advance if it won the prior round). This models realistic pool competition rather than uniform random opponents.

## Web Interface

The Flask web app provides:

- **Pool configuration**: Pool size, scoring preset, strategy balance slider
- **Custom scoring**: Per-round point values
- **Upset bonuses**: Seed difference multiplier or fixed bonus per round
- **Team biases**: Mark teams as over/under-picked in your specific pool
- **Force champion**: Lock in a specific champion pick
- **Multiple rating sources**: Choose individual sources or consensus blend
- **Background job queue**: Optimization runs asynchronously with progress tracking
- **HTML bracket export**: Self-contained bracket with inline stats (p(reach), public %) and detailed tooltips

### Running the Web App

```bash
pip install -r requirements.txt
python -m web.app
```

Then visit `http://localhost:17349`. Refresh data sources at `/admin/refresh?key=refresh`.

## CLI Usage

```bash
# Fetch ratings and bracket
python cli.py fetch-ratings --source torvik
python cli.py load-bracket --file bracket.json

# Fetch public pick data
python cli.py fetch-picks --source espn

# Run simulation and optimization
python cli.py simulate --sims 10000
python cli.py optimize --pool-size 7 --accuracy-weight 0.75

# Export
python cli.py export --format yahoo
```

## Key Insight

The fundamental insight is that **pool-winning brackets are not the same as high-scoring brackets**. A bracket that scores 80 points but shares 60 of those points with half the pool will lose to a bracket that scores 70 points but has 40 unique points. Seed Money finds the bracket that maximizes the probability of having the highest score in your specific pool — accounting for pool size, scoring rules, and what your opponents are likely to pick.
