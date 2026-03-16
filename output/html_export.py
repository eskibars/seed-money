"""HTML+CSS bracket export.

Generates a self-contained HTML file showing the full 64-team bracket
in the classic NCAA tournament visual layout: two regions on the left,
two on the right, converging to Final Four in the center.
"""

import os
from models.bracket import Bracket
from models.team import Team
from optimizer.pick_utils import default_pick_pct, get_pick_pct


def export_bracket_html(bracket: Bracket, filepath: str,
                        reach_probs: dict[str, dict[int, float]] | None = None,
                        pick_pcts: dict[str, dict[int, float]] | None = None,
                        title: str = "Seed Money — Optimized Bracket"):
    """Export the bracket as a self-contained HTML file.

    Args:
        bracket: Completed bracket with all 63 picks
        filepath: Output HTML file path
        reach_probs: Optional probability data for tooltips
        pick_pcts: Optional pick popularity data for tooltips
        title: Page title
    """
    # Build the data structures the template needs
    left_top = _region_data(bracket, 0, reach_probs, pick_pcts)     # e.g. East
    left_bot = _region_data(bracket, 1, reach_probs, pick_pcts)     # e.g. West
    right_top = _region_data(bracket, 2, reach_probs, pick_pcts)    # e.g. South
    right_bot = _region_data(bracket, 3, reach_probs, pick_pcts)    # e.g. Midwest

    final_four = _final_four_data(bracket, reach_probs, pick_pcts)

    html = _render_html(title, left_top, left_bot, right_top, right_bot, final_four)

    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Exported HTML bracket to {filepath}")


def _team_cell(team: Team | None, is_winner: bool = False,
               reach_probs=None, pick_pcts=None, round_num=None) -> dict:
    """Build a dict describing one team cell."""
    if team is None:
        return {"name": "", "seed": "", "classes": "empty", "tooltip": ""}

    classes = []
    if is_winner:
        classes.append("winner")

    tooltip_parts = [f"{team.name} ({team.seed} seed)"]
    if reach_probs and round_num:
        p = reach_probs.get(team.name, {}).get(round_num, 0)
        tooltip_parts.append(f"P(reach): {p:.1%}")
    if pick_pcts:
        pick_round = round_num or 2
        pp = get_pick_pct(pick_pcts, team.name, pick_round, default_pick_pct(team.seed, pick_round))
        tooltip_parts.append(f"Public: {pp:.1%}")

    return {
        "name": team.name,
        "seed": team.seed,
        "classes": " ".join(classes),
        "tooltip": " | ".join(tooltip_parts),
    }


def _region_data(bracket, region_idx, reach_probs, pick_pcts):
    """Extract all rounds for one region."""
    region_name = bracket.regions.get(region_idx, f"Region {region_idx + 1}")
    base = 64 + region_idx * 16

    # Round 1: 8 matchups, each is a pair of starting teams + winner
    r1 = []
    game_base = 32 + region_idx * 8
    for i in range(8):
        slot = game_base + i
        left = bracket.slots[base + 2 * i]
        right = bracket.slots[base + 2 * i + 1]
        winner = bracket.slots[slot]
        r1.append({
            "top": _team_cell(left),
            "bot": _team_cell(right),
            "winner": _team_cell(winner, True, reach_probs, pick_pcts, 2),
        })

    # Round 2: 4 matchups
    r2 = []
    r2_base = 16 + region_idx * 4
    for i in range(4):
        slot = r2_base + i
        winner = bracket.slots[slot]
        r2.append({"winner": _team_cell(winner, True, reach_probs, pick_pcts, 3)})

    # Sweet 16: 2 matchups
    r3 = []
    s16_base = 8 + region_idx * 2
    for i in range(2):
        slot = s16_base + i
        winner = bracket.slots[slot]
        r3.append({"winner": _team_cell(winner, True, reach_probs, pick_pcts, 4)})

    # Elite 8: 1 matchup (regional final)
    e8_slot = 4 + region_idx
    e8_winner = bracket.slots[e8_slot]
    r4 = {"winner": _team_cell(e8_winner, True, reach_probs, pick_pcts, 5)}

    return {
        "name": region_name,
        "r1": r1,
        "r2": r2,
        "r3": r3,
        "r4": r4,
    }


def _final_four_data(bracket, reach_probs, pick_pcts):
    """Extract Final Four and Championship data."""
    sf1 = bracket.slots[2]
    sf2 = bracket.slots[3]
    champ = bracket.slots[1]

    # F4 teams (the 4 regional winners)
    f4 = [bracket.slots[4 + i] for i in range(4)]

    return {
        "sf1_winner": _team_cell(sf1, True, reach_probs, pick_pcts, 6),
        "sf2_winner": _team_cell(sf2, True, reach_probs, pick_pcts, 6),
        "champion": _team_cell(champ, True, reach_probs, pick_pcts, 7),
        "f4_teams": [_team_cell(t, True, reach_probs, pick_pcts, 5) for t in f4],
    }


def _render_team(cell: dict) -> str:
    """Render a single team cell as HTML."""
    if not cell["name"]:
        return '<div class="team empty">&nbsp;</div>'
    seed = cell["seed"]
    name = cell["name"]
    tip = cell["tooltip"]
    cls = f'team {cell["classes"]}'.strip()
    return f'<div class="{cls}" title="{tip}"><span class="seed">{seed}</span> {name}</div>'


def _render_matchup_r1(matchup: dict) -> str:
    """Render a Round 1 matchup (two teams)."""
    return f'''<div class="matchup">
  {_render_team(matchup["top"])}
  {_render_team(matchup["bot"])}
</div>'''


def _render_later_round(cell: dict) -> str:
    """Render a later-round slot (just the winner advancing)."""
    return f'<div class="matchup later">{_render_team(cell["winner"])}</div>'


def _render_region_left(region: dict) -> str:
    """Render a left-side region (rounds flow left-to-right)."""
    name = region["name"]

    r1_html = "\n".join(_render_matchup_r1(m) for m in region["r1"])
    r1w_html = "\n".join(_render_later_round(m) for m in region["r1"])
    r2_html = "\n".join(_render_later_round(m) for m in region["r2"])
    r3_html = "\n".join(_render_later_round(m) for m in region["r3"])
    r4_html = _render_later_round(region["r4"])

    return f'''<div class="region left">
  <div class="region-label">{name}</div>
  <div class="rounds">
    <div class="round r1">{r1_html}</div>
    <div class="round r1w">{r1w_html}</div>
    <div class="round r2">{r2_html}</div>
    <div class="round r3">{r3_html}</div>
    <div class="round r4">{r4_html}</div>
  </div>
</div>'''


def _render_region_right(region: dict) -> str:
    """Render a right-side region (rounds flow right-to-left)."""
    name = region["name"]

    r1_html = "\n".join(_render_matchup_r1(m) for m in region["r1"])
    r1w_html = "\n".join(_render_later_round(m) for m in region["r1"])
    r2_html = "\n".join(_render_later_round(m) for m in region["r2"])
    r3_html = "\n".join(_render_later_round(m) for m in region["r3"])
    r4_html = _render_later_round(region["r4"])

    return f'''<div class="region right">
  <div class="region-label">{name}</div>
  <div class="rounds">
    <div class="round r4">{r4_html}</div>
    <div class="round r3">{r3_html}</div>
    <div class="round r2">{r2_html}</div>
    <div class="round r1w">{r1w_html}</div>
    <div class="round r1">{r1_html}</div>
  </div>
</div>'''


def _render_html(title, left_top, left_bot, right_top, right_bot, final_four):
    """Render the full HTML page."""

    lt = _render_region_left(left_top)
    lb = _render_region_left(left_bot)
    rt = _render_region_right(right_top)
    rb = _render_region_right(right_bot)

    champ = final_four["champion"]
    sf1 = final_four["sf1_winner"]
    sf2 = final_four["sf2_winner"]

    if champ["name"]:
        champ_inner = f'<span class="seed">{champ["seed"]}</span> {champ["name"]}'
    else:
        champ_inner = "&nbsp;"
    champ_tooltip = champ["tooltip"]

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1400">
<title>{title}</title>
<style>
  :root {{
    --team-h: 22px;
    --team-w: 150px;
    --gap: 2px;
    --matchup-h: calc(var(--team-h) * 2 + var(--gap));
    --round-gap: 6px;
    --color-bg: #1a1a2e;
    --color-surface: #16213e;
    --color-border: #0f3460;
    --color-text: #e4e4e4;
    --color-seed: #e94560;
    --color-winner: #00b4d8;
    --color-champ: #ffd700;
    --color-region: #e94560;
    --color-accent: #0f3460;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    background: var(--color-bg);
    color: var(--color-text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 12px;
    padding: 20px 10px;
  }}

  h1 {{
    text-align: center;
    font-size: 22px;
    color: var(--color-champ);
    margin-bottom: 4px;
    letter-spacing: 1px;
  }}

  .subtitle {{
    text-align: center;
    color: #888;
    font-size: 11px;
    margin-bottom: 16px;
  }}

  /* ---------- MAIN GRID ---------- */
  .bracket {{
    display: flex;
    max-width: 1600px;
    margin: 0 auto;
    align-items: stretch;
  }}

  .left-col, .right-col {{
    display: flex;
    flex-direction: column;
    gap: 20px;
    flex: 1;
    min-width: 0;
  }}

  /* ---------- REGION ---------- */
  .region {{
    flex: 1;
  }}

  .region-label {{
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--color-region);
    margin-bottom: 6px;
    padding-left: 4px;
  }}

  .region.right .region-label {{
    text-align: right;
    padding-right: 4px;
    padding-left: 0;
  }}

  .rounds {{
    display: flex;
    align-items: stretch;
  }}

  .round {{
    display: flex;
    flex-direction: column;
    justify-content: space-around;
    margin-right: var(--round-gap);
    min-width: var(--team-w);
  }}

  .region.right .rounds {{
    flex-direction: row;
  }}

  .region.right .round {{
    margin-right: 0;
    margin-left: var(--round-gap);
  }}

  /* ---------- MATCHUP & TEAM ---------- */
  .matchup {{
    display: flex;
    flex-direction: column;
    gap: var(--gap);
    margin: 2px 0;
  }}

  .team {{
    height: var(--team-h);
    line-height: var(--team-h);
    padding: 0 6px;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    cursor: default;
    transition: background 0.15s;
    font-size: 11px;
  }}

  .team:hover {{
    background: var(--color-accent);
  }}

  .team.empty {{
    visibility: hidden;
  }}

  .team.winner {{
    color: var(--color-winner);
    font-weight: 600;
    border-color: var(--color-winner);
  }}

  .seed {{
    display: inline-block;
    min-width: 16px;
    text-align: center;
    font-weight: 700;
    color: var(--color-seed);
    margin-right: 2px;
    font-size: 10px;
  }}

  /* ---------- FINAL FOUR CENTER ---------- */
  .center {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 0 18px;
    gap: 10px;
    min-width: 200px;
  }}

  .ff-label {{
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--color-region);
  }}

  .ff-matchup {{
    display: flex;
    flex-direction: column;
    gap: var(--gap);
    width: 170px;
  }}

  .ff-matchup .team {{
    min-width: 160px;
  }}

  .championship {{
    text-align: center;
    margin: 6px 0;
  }}

  .champ-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--color-champ);
    margin-bottom: 4px;
  }}

  .champion-team {{
    height: 32px;
    line-height: 32px;
    padding: 0 14px;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border: 2px solid var(--color-champ);
    border-radius: 5px;
    color: var(--color-champ);
    font-weight: 700;
    font-size: 14px;
    text-align: center;
    white-space: nowrap;
  }}

  .champion-team .seed {{
    color: var(--color-champ);
    font-size: 12px;
  }}

  /* ---------- SCORING KEY ---------- */
  .footer {{
    text-align: center;
    margin-top: 18px;
    color: #666;
    font-size: 10px;
  }}

  .support-link {{
    margin-top: 8px;
    font-size: 11px;
  }}

  .support-link a {{
    color: var(--color-champ);
    font-weight: 700;
    text-decoration: none;
  }}

  .support-link a:hover {{
    text-decoration: underline;
  }}

  .scoring {{
    display: inline-flex;
    gap: 12px;
    margin-top: 4px;
  }}

  .scoring span {{
    color: #888;
  }}

  /* ---------- PRINT ---------- */
  @media print {{
    body {{ background: #fff; color: #222; padding: 0; font-size: 10px; }}
    .team {{ border-color: #ccc; background: #fff; }}
    .team.winner {{ color: #0066cc; border-color: #0066cc; }}
    .seed {{ color: #cc0000; }}
    .region-label, .ff-label {{ color: #cc0000; }}
    .champion-team {{ border-color: #cc8800; color: #cc8800; background: #fff; }}
    .champion-team .seed {{ color: #cc8800; }}
    h1 {{ color: #222; }}
    .support-link a {{ color: #222; }}
    :root {{ --team-w: 130px; --team-h: 18px; }}
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">Hover over teams for probabilities</div>

<div class="bracket">
  <!-- LEFT: Regions 0 (top) and 1 (bottom) -->
  <div class="left-col">
    {lt}
    {lb}
  </div>

  <!-- CENTER: Final Four + Championship -->
  <div class="center">
    <div class="ff-label">Final Four</div>
    <div class="ff-matchup">
      {_render_team(final_four["f4_teams"][0])}
      {_render_team(final_four["f4_teams"][1])}
    </div>
    <div class="ff-matchup">
      {_render_team(sf1)}
    </div>

    <div class="championship">
      <div class="champ-label">Champion</div>
      <div class="champion-team" title="{champ_tooltip}">
        {champ_inner}
      </div>
    </div>

    <div class="ff-matchup">
      {_render_team(sf2)}
    </div>
    <div class="ff-matchup">
      {_render_team(final_four["f4_teams"][2])}
      {_render_team(final_four["f4_teams"][3])}
    </div>
  </div>

  <!-- RIGHT: Regions 2 (top) and 3 (bottom) -->
  <div class="right-col">
    {rt}
    {rb}
  </div>
</div>

<div class="footer">
  Generated by Seed Money &mdash; March Madness Bracket Optimizer
  <div class="scoring">
    <span>R64: 1pt</span>
    <span>R32: 2pts</span>
    <span>S16: 3pts</span>
    <span>E8: 4pts</span>
    <span>F4: 4pts</span>
    <span>Champ: 5pts</span>
  </div>
  <div class="support-link">
    <a href="https://buymeacoffee.com/shane.connelly" target="_blank" rel="noopener noreferrer">Buy Me a Coffee</a>
  </div>
</div>
</body>
</html>'''
