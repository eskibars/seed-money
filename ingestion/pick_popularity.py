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
LEGACY_ESPN_PICKS_URLS = [
    "https://fantasy.espn.com/games/tournament-challenge-bracket/en/whopickedwhom",
    "https://fantasy.espn.com/games/tournament-challenge-bracket-{year}/whopickedwhom",
    "https://fantasy.espn.com/games/tournament-challenge-bracket-{year}/popular",
]

def fetch_espn_picks(year: int = 2026,
                     save: bool = True,
                     ratings: dict[str, dict] | None = None,
                     challenge_id: int | None = None,
                     bracket_teams: set[str] | None = None) -> dict[str, dict[int, float]]:
    """Fetch ESPN public pick percentages from the current JSON API."""
    ratings = ratings or {}
    resolver = _build_name_resolver(ratings)
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    partial_pick_sets = []
    raw_payloads: dict[str, list[dict]] = {}
    resolved_challenge_id = _resolve_espn_challenge_id(year, challenge_id)
    api_error: Exception | None = None

    if resolved_challenge_id is None:
        print(
            f"Warning: No ESPN Tournament Challenge challengeId configured for {year}; "
            "trying legacy HTML fallback."
        )
    else:
        for scoring_period_id in config.ESPN_PICKS_SCORING_PERIODS:
            try:
                response = session.get(
                    config.ESPN_PICKS_PROPOSITIONS_URL,
                    params={
                        "challengeId": resolved_challenge_id,
                        "scoringPeriodId": scoring_period_id,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                raw_payloads[str(scoring_period_id)] = payload

                round_picks = _parse_espn_propositions(
                    payload,
                    resolver=resolver,
                    ratings=ratings,
                    round_reaching=scoring_period_id + 1,
                    bracket_teams=bracket_teams,
                )
                if round_picks:
                    partial_pick_sets.append(round_picks)
            except (requests.RequestException, ValueError) as exc:
                api_error = exc

        if save and raw_payloads:
            _save_json(f"espn_picks_{year}.json", raw_payloads)

        picks = merge_pick_pcts(partial_pick_sets)
        if picks:
            return picks

    legacy_error: Exception | None = None
    for url_template in LEGACY_ESPN_PICKS_URLS:
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
            legacy_error = exc

    if api_error is not None:
        print(f"Warning: Could not fetch ESPN picks from gambit API: {api_error}")
    if legacy_error is not None:
        print(f"Warning: Could not fetch ESPN picks from legacy pages: {legacy_error}")
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


def _parse_espn_propositions(payload,
                             resolver,
                             ratings: dict[str, dict],
                             round_reaching: int,
                             bracket_teams: set[str] | None = None) -> dict[str, dict[int, float]]:
    """Parse ESPN's current Tournament Challenge propositions API."""
    if isinstance(payload, dict):
        propositions = payload.get("items") or payload.get("propositions") or []
    else:
        propositions = payload or []

    picks: dict[str, dict[int, float]] = {}
    for proposition in propositions:
        for outcome in proposition.get("possibleOutcomes") or []:
            pct = _extract_espn_outcome_percentage(outcome)
            if pct is None:
                continue

            for team_name in _resolve_espn_outcome_team_names(
                outcome,
                resolver,
                ratings,
                bracket_teams=bracket_teams,
            ):
                picks.setdefault(team_name, {})[round_reaching] = pct

    return picks


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


def _extract_espn_outcome_percentage(outcome: dict) -> float | None:
    """Extract a pick percentage from one ESPN proposition outcome."""
    counters = outcome.get("choiceCounters") or []

    preferred_counter = None
    for counter in counters:
        if counter.get("percentage") is None:
            continue
        if int(counter.get("scoringFormatId") or 0) == 5:
            preferred_counter = counter
            break

    if preferred_counter is None:
        preferred_counter = next(
            (counter for counter in counters if counter.get("percentage") is not None),
            None,
        )

    if preferred_counter is None:
        return None
    return _coerce_pct(preferred_counter.get("percentage"))


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
    aliases = _load_team_aliases()

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


def _load_team_aliases() -> dict[str, str]:
    """Load canonical team-name aliases used across source ingestors."""
    aliases_path = os.path.join(os.path.dirname(__file__), "..", "data", "team_aliases.json")
    if not os.path.exists(aliases_path):
        return {}
    with open(aliases_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_espn_challenge_id(year: int, explicit_challenge_id: int | None) -> int | None:
    """Resolve which ESPN Tournament Challenge challengeId to query."""
    if explicit_challenge_id is not None:
        return int(explicit_challenge_id)

    env_specific = os.environ.get(f"SEED_MONEY_ESPN_CHALLENGE_ID_{year}", "").strip()
    if env_specific:
        return int(env_specific)

    env_default = os.environ.get("SEED_MONEY_ESPN_CHALLENGE_ID", "").strip()
    if env_default:
        return int(env_default)

    return config.ESPN_TOURNAMENT_CHALLENGE_IDS.get(year)


def _resolve_espn_outcome_team_names(outcome: dict,
                                     resolver,
                                     ratings: dict[str, dict],
                                     bracket_teams: set[str] | None = None) -> list[str]:
    """Resolve an ESPN outcome to one or more canonical team names."""
    resolved = []
    for value in (outcome.get("name"), outcome.get("description")):
        text = _clean_team_name(str(value or ""))
        if not text:
            continue
        if "/" in text:
            resolved.extend(
                _expand_slash_separated_team_names(
                    text,
                    resolver,
                    ratings,
                    bracket_teams=bracket_teams,
                )
            )
            continue
        resolved.append(resolver(text))
        break

    if not resolved:
        abbrev = _clean_team_name(str(outcome.get("abbrev") or ""))
        if "/" in abbrev:
            resolved.extend(
                _expand_slash_separated_team_names(
                    abbrev,
                    resolver,
                    ratings,
                    bracket_teams=bracket_teams,
                )
            )
        elif abbrev:
            resolved.append(resolver(abbrev))

    return _dedupe_preserve_order(name for name in resolved if name)


def _expand_slash_separated_team_names(text: str,
                                       resolver,
                                       ratings: dict[str, dict],
                                       bracket_teams: set[str] | None = None) -> list[str]:
    """Expand a placeholder like 'UMBC/HOW' into real team names when possible."""
    aliases = _load_team_aliases()
    code_to_canonical: dict[str, set[str]] = {}

    candidate_names = set(bracket_teams or ())
    if not candidate_names:
        candidate_names = set(ratings.keys())

    for canonical in candidate_names:
        for code in _build_matching_codes(canonical):
            code_to_canonical.setdefault(code, set()).add(canonical)

    for alias, canonical in aliases.items():
        if alias.startswith("_comment"):
            continue
        if candidate_names and canonical not in candidate_names:
            continue
        for code in _build_matching_codes(alias):
            code_to_canonical.setdefault(code, set()).add(canonical)

    resolved = []
    for segment in re.split(r"/+", text):
        cleaned_segment = _clean_team_name(segment)
        if not cleaned_segment:
            continue

        direct = resolver(cleaned_segment) or cleaned_segment
        if candidate_names and direct not in candidate_names:
            direct = ""
        if direct:
            resolved.append(direct)
            continue

        normalized_segment = _normalize_code(cleaned_segment)
        exact_matches = sorted(code_to_canonical.get(normalized_segment, ()))
        if len(exact_matches) == 1:
            resolved.extend(exact_matches)
            continue

        resolved.append(cleaned_segment)

    return _dedupe_preserve_order(resolved)


def _build_matching_codes(text: str) -> set[str]:
    """Build short codes for matching compact ESPN placeholder segments."""
    normalized = _normalize_code(text)
    tokens = [
        _normalize_code(token)
        for token in re.split(r"[^A-Za-z0-9]+", text or "")
        if token
    ]
    initials = "".join(token[:1] for token in tokens)
    consonants = re.sub(r"[AEIOU]", "", normalized)

    codes = {normalized, initials, consonants}
    for value in (normalized, consonants):
        if len(value) >= 2:
            codes.add(value[:2])
        if len(value) >= 3:
            codes.add(value[:3])
        if len(value) >= 4:
            codes.add(value[:4])
    return {code for code in codes if code}


def _normalize_code(text: str) -> str:
    """Normalize a compact code like 'M-OH' into 'MOH'."""
    return re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())


def _dedupe_preserve_order(values) -> list[str]:
    """Remove duplicates while preserving the original order."""
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


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
