"""Public pick percentage ingestion from multiple bracket sources."""

from __future__ import annotations

import html as html_lib
import json
import os
import re

import requests
from bs4 import BeautifulSoup

import config
from optimizer.pick_utils import merge_pick_pcts

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/json,text/plain,*/*",
}

YAHOO_PICKS_URL = "https://tournament.fantasysports.yahoo.com/mens-basketball-bracket/pickdistribution"
ESPN_PICKS_URLS = [
    "https://fantasy.espn.com/games/tournament-challenge-bracket/en/whopickedwhom",
    "https://fantasy.espn.com/games/tournament-challenge-bracket-{year}/whopickedwhom",
    "https://fantasy.espn.com/games/tournament-challenge-bracket-{year}/popular",
]

def fetch_espn_picks(year: int = 2026,
                     save: bool = True,
                     ratings: dict[str, dict] | None = None) -> dict[str, dict[int, float]]:
    """Fetch ESPN public pick percentages from known Tournament Challenge pages."""
    resolver = _build_name_resolver(ratings or {})
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    last_error: Exception | None = None
    for url_template in ESPN_PICKS_URLS:
        url = url_template.format(year=year)
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            if save:
                _save_text(f"espn_picks_{year}.html", response.text)

            picks = _parse_espn_picks(response.text, resolver)
            if picks:
                return picks
        except requests.RequestException as exc:
            last_error = exc

    if last_error is not None:
        print(f"Warning: Could not fetch ESPN picks: {last_error}")
    return {}


def fetch_yahoo_picks(year: int = 2026,
                      game_key: int | None = None,
                      save: bool = True,
                      ratings: dict[str, dict] | None = None) -> dict[str, dict[int, float]]:
    """Fetch Yahoo public pick percentages from the bracket API and page."""
    from ingestion.bracket_fetcher import _discover_yahoo_bracket_payload

    resolver = _build_name_resolver(ratings or {})
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    try:
        payload, resolved_game_key, _ = _discover_yahoo_bracket_payload(
            session=session,
            year=year,
            game_key=game_key,
        )
        if save:
            _save_json(f"yahoo_pick_distribution_{year}.json", payload)

        picks = _parse_yahoo_api_picks(payload, resolver)
        if picks:
            return picks

        print(
            f"Yahoo pick distribution for {year} is present but empty "
            f"(gameKey={resolved_game_key})."
        )
    except Exception as exc:
        print(f"Warning: Could not fetch Yahoo pick API data: {exc}")

    try:
        response = session.get(YAHOO_PICKS_URL, timeout=30)
        response.raise_for_status()
        if save:
            _save_text(f"yahoo_picks_{year}.html", response.text)
        return _parse_yahoo_html_picks(response.text, resolver)
    except requests.RequestException as exc:
        print(f"Warning: Could not fetch Yahoo pick page: {exc}")
        return {}


def fetch_ncaa_picks(save: bool = True,
                     ratings: dict[str, dict] | None = None) -> dict[str, dict[int, float]]:
    """Fetch optional NCAA article-based partial pick data from configured URLs."""
    return _fetch_article_source_picks("ncaa", config.NCAA_PICK_ARTICLE_URLS, save=save, ratings=ratings)


def fetch_cbs_picks(save: bool = True,
                    ratings: dict[str, dict] | None = None) -> dict[str, dict[int, float]]:
    """Fetch optional CBS article-based partial pick data from configured URLs."""
    return _fetch_article_source_picks("cbs", config.CBS_PICK_ARTICLE_URLS, save=save, ratings=ratings)


def _fetch_article_source_picks(source_name: str,
                                url_map: dict[int, str],
                                save: bool,
                                ratings: dict[str, dict] | None) -> dict[str, dict[int, float]]:
    """Fetch partial pick percentages from configured article URLs."""
    resolver = _build_name_resolver(ratings or {})
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    partial_pick_sets = []
    for round_reaching, url in url_map.items():
        url = (url or "").strip()
        if not url:
            continue
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            if save:
                _save_text(f"{source_name}_picks_r{round_reaching}.html", response.text)

            picks = _parse_article_pick_percentages(response.text, round_reaching, resolver)
            if picks:
                partial_pick_sets.append(picks)
        except requests.RequestException as exc:
            print(f"Warning: Could not fetch {source_name.upper()} picks from {url}: {exc}")

    if not partial_pick_sets:
        return {}

    return merge_pick_pcts(partial_pick_sets)


def _parse_yahoo_api_picks(payload: dict, resolver) -> dict[str, dict[int, float]]:
    """Parse Yahoo API pick distribution data."""
    fantasy_game = payload.get("data", {}).get("fantasyGame") or {}
    tournament = fantasy_game.get("tournament") or {}
    team_names_by_key = _build_team_name_lookup(tournament, resolver)
    pick_distribution = fantasy_game.get("pickDistribution") or {}
    rounds = pick_distribution.get("distributionByRound") or []

    picks: dict[str, dict[int, float]] = {}
    for round_entry in rounds:
        round_reaching = int(round_entry.get("roundId", 0)) + 1
        if round_reaching < 2 or round_reaching > 7:
            continue

        for team_entry in round_entry.get("distributionByTeam") or []:
            team_name = _resolve_distribution_team_name(team_entry, team_names_by_key, resolver)
            pct = _extract_percentage(team_entry)
            if not team_name or pct is None:
                continue
            picks.setdefault(team_name, {})[round_reaching] = pct

    return picks


def _parse_yahoo_html_picks(html: str, resolver) -> dict[str, dict[int, float]]:
    """Parse Yahoo pick-distribution HTML as a fallback."""
    picks = _parse_multi_round_pick_table_html(html, resolver)
    if picks:
        return picks
    return _parse_pick_table_html(html, resolver, round_reaching=None)


def _parse_espn_picks(html: str, resolver) -> dict[str, dict[int, float]]:
    """Parse ESPN pick pages from HTML tables."""
    picks = _parse_multi_round_pick_table_html(html, resolver)
    if picks:
        return picks
    return _parse_pick_table_html(html, resolver, round_reaching=None)


def _parse_multi_round_pick_table_html(html: str, resolver) -> dict[str, dict[int, float]]:
    """Parse an HTML table with multiple advancement-percentage columns."""
    soup = BeautifulSoup(html, "html.parser")
    best_picks: dict[str, dict[int, float]] = {}

    for table in soup.find_all("table"):
        table_picks = _extract_multi_round_pick_rows_from_table(table, resolver)
        if len(table_picks) > len(best_picks):
            best_picks = table_picks

    return best_picks


def _parse_pick_table_html(html: str, resolver, round_reaching: int | None) -> dict[str, dict[int, float]]:
    """Parse a generic HTML table of team pick percentages."""
    soup = BeautifulSoup(html, "html.parser")
    best_rows: list[tuple[str, float]] = []

    for table in soup.find_all("table"):
        parsed_rows = _extract_pick_rows_from_table(table, resolver)
        if len(parsed_rows) > len(best_rows):
            best_rows = parsed_rows

    if not best_rows:
        return {}

    picks: dict[str, dict[int, float]] = {}
    if round_reaching is not None:
        for team_name, pct in best_rows:
            picks.setdefault(team_name, {})[round_reaching] = pct
        return picks

    # Generic tables typically list one percentage per team. Treat that as
    # a round-2 signal only when nothing richer is available.
    for team_name, pct in best_rows:
        picks.setdefault(team_name, {})[2] = pct
    return picks


def _parse_article_pick_percentages(html: str,
                                    round_reaching: int,
                                    resolver) -> dict[str, dict[int, float]]:
    """Parse article tables for champion/Final Four/upset percentages."""
    return _parse_pick_table_html(html, resolver, round_reaching=round_reaching)


def _extract_pick_rows_from_table(table, resolver) -> list[tuple[str, float]]:
    """Extract (team, pct) rows from a table when possible."""
    rows = table.find_all("tr")
    if not rows:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    headers = [_normalize_text(cell.get_text(" ", strip=True)).lower() for cell in header_cells]

    pct_idx = None
    team_idx = None
    for idx, header in enumerate(headers):
        if pct_idx is None and ("%" in header or "pick" in header):
            pct_idx = idx
        if team_idx is None and "team" in header:
            team_idx = idx

    parsed_rows: list[tuple[str, float]] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        texts = [_normalize_text(cell.get_text(" ", strip=True)) for cell in cells]
        pct = _parse_pct(texts[pct_idx]) if pct_idx is not None and pct_idx < len(texts) else None
        if pct is None:
            for text in texts:
                pct = _parse_pct(text)
                if pct is not None:
                    break
        if pct is None:
            continue

        candidate_texts = list(texts)
        if pct_idx is not None and pct_idx < len(candidate_texts):
            candidate_texts.pop(pct_idx)

        if team_idx is not None and team_idx < len(texts):
            team_name = _clean_team_name(texts[team_idx])
        else:
            team_name = ""
            for text in candidate_texts:
                cleaned = _clean_team_name(text)
                if cleaned:
                    team_name = cleaned
                    break

        if not team_name:
            continue

        parsed_rows.append((resolver(team_name), pct))

    return parsed_rows


def _extract_multi_round_pick_rows_from_table(table, resolver) -> dict[str, dict[int, float]]:
    """Extract {team: {round: pct}} rows from a table with multiple % columns."""
    rows = table.find_all("tr")
    if not rows:
        return {}

    header_cells = rows[0].find_all(["th", "td"])
    headers = [_normalize_text(cell.get_text(" ", strip=True)).lower() for cell in header_cells]

    team_idx = None
    pct_indices = []
    for idx, header in enumerate(headers):
        if team_idx is None and "team" in header:
            team_idx = idx
        if "%" in header or "pick" in header:
            pct_indices.append(idx)

    if team_idx is None or len(pct_indices) < 2:
        return {}

    picks: dict[str, dict[int, float]] = {}
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(team_idx, max(pct_indices)):
            continue

        texts = [_normalize_text(cell.get_text(" ", strip=True)) for cell in cells]
        team_name = resolver(_clean_team_name(texts[team_idx]))
        if not team_name:
            continue

        rounds: dict[int, float] = {}
        round_reaching = 2
        for idx in pct_indices:
            pct = _parse_pct(texts[idx])
            if pct is not None:
                rounds[round_reaching] = pct
                round_reaching += 1

        if rounds:
            picks[team_name] = rounds

    return picks


def _build_team_name_lookup(tournament: dict, resolver) -> dict[str, str]:
    """Map Yahoo editorial team keys to normalized team names."""
    teams = {}
    for item in tournament.get("tournamentTeams") or []:
        key = item.get("editorialTeamKey")
        editorial_team = item.get("editorialTeam") or {}
        display_name = editorial_team.get("displayName")
        if not key or not display_name:
            continue
        teams[str(key)] = resolver(display_name)
    return teams


def _resolve_distribution_team_name(team_entry: dict,
                                    team_names_by_key: dict[str, str],
                                    resolver) -> str:
    """Resolve a team name from a distribution entry."""
    editorial_team = team_entry.get("editorialTeam") or {}
    team_key = team_entry.get("editorialTeamKey") or editorial_team.get("editorialTeamKey")
    if team_key and str(team_key) in team_names_by_key:
        return team_names_by_key[str(team_key)]

    for value in (
        editorial_team.get("displayName"),
        team_entry.get("displayName"),
        team_entry.get("teamName"),
        team_entry.get("name"),
    ):
        if value:
            return resolver(_clean_team_name(str(value)))

    return ""


def _extract_percentage(team_entry: dict) -> float | None:
    """Find a percentage-like numeric field in a source entry."""
    preferred_keys = [
        "pickPercentage",
        "pickPercent",
        "pickedPercentage",
        "pickedPercent",
        "percentPicked",
        "pickPct",
        "pickedPct",
    ]
    for key in preferred_keys:
        if key in team_entry:
            return _coerce_pct(team_entry[key])

    for key, value in team_entry.items():
        if isinstance(value, (int, float)):
            key_lower = key.lower()
            if "count" in key_lower or "seed" in key_lower or "rank" in key_lower:
                continue
            if any(token in key_lower for token in ("pct", "percent", "percentage", "pick")):
                return _coerce_pct(value)

    return None


def _coerce_pct(value) -> float | None:
    """Convert a numeric percent or fraction to a 0-1 float."""
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None

    if pct < 0:
        return None
    if pct > 1:
        pct /= 100.0
    if pct > 1:
        return None
    return pct


def _parse_pct(text: str) -> float | None:
    """Parse text like '27.4%' into a 0-1 fraction."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return float(match.group(1)) / 100.0


def _clean_team_name(text: str) -> str:
    """Strip seeds, ranking labels, and extra formatting from team names."""
    text = _normalize_text(text)
    text = re.sub(r"^No\.\s*\d+\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\(\s*\d+\s*\)\s*", "", text)
    text = re.sub(r"\(\s*\d+\s*\)$", "", text)
    text = re.sub(r"\(\s*\d+\s*,[^)]*\)$", "", text)
    return text.strip()


def _build_name_resolver(ratings: dict[str, dict]):
    """Create a best-effort public-name to canonical-name resolver."""
    aliases_path = os.path.join(os.path.dirname(__file__), "..", "data", "team_aliases.json")
    aliases: dict[str, str] = {}
    if os.path.exists(aliases_path):
        with open(aliases_path, "r", encoding="utf-8") as f:
            aliases = json.load(f)

    normalized_to_canonical: dict[str, str] = {}
    for canonical in ratings:
        normalized_to_canonical[_normalize_text(canonical).lower()] = canonical

    for alias, canonical in aliases.items():
        if alias.startswith("_comment"):
            continue
        normalized_to_canonical[_normalize_text(alias).lower()] = canonical
        normalized_to_canonical.setdefault(_normalize_text(canonical).lower(), canonical)

    def resolve(name: str) -> str:
        return normalized_to_canonical.get(_normalize_text(name).lower(), name)

    return resolve


def _normalize_text(text: str) -> str:
    """Normalize whitespace and punctuation for matching."""
    replacements = {
        "\xa0": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u2013": "-",
        "\u2014": "-",
    }
    text = html_lib.unescape(text or "")
    for src, dest in replacements.items():
        text = text.replace(src, dest)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _save_text(filename: str, text: str):
    """Save raw text/HTML for debugging."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _save_json(filename: str, payload: dict):
    """Save raw JSON for debugging."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def save_picks(picks: dict[str, dict[int, float]], filepath: str):
    """Save parsed pick percentages to JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    serializable = {name: {str(r): p for r, p in rpcts.items()} for name, rpcts in picks.items()}
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)
    print(f"Saved pick percentages to {filepath}")


def load_picks(filepath: str) -> dict[str, dict[int, float]]:
    """Load previously saved pick percentages from JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {name: {int(r): p for r, p in rpcts.items()} for name, rpcts in data.items()}
