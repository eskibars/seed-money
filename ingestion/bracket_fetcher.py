"""Public bracket ingestion from Yahoo's tournament API."""

from __future__ import annotations

import html as html_lib
import json
import os
import re
from typing import Iterable

import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
YAHOO_BRACKET_URL = (
    "https://pylon.sports.yahoo.com/v1/gql/call/tourney/bracket"
    "?gameKey={game_key}&yspRemoveNulls=true&ysp_src=tdv2-app-fantasy"
)
YAHOO_MENS_URL_FRAGMENT = "mens-basketball-bracket"
KNOWN_YAHOO_MENS_GAME_KEYS = {
    2023: 420,
    2024: 429,
    2025: 459,
    2026: 467,
}
REGION_POSITION_ORDER = {
    "top-left": 0,
    "bottom-left": 1,
    "top-right": 2,
    "bottom-right": 3,
}


def fetch_yahoo_bracket(year: int = 2026,
                        game_key: int | None = None,
                        ratings: dict[str, dict] | None = None,
                        save: bool = True) -> tuple[dict, dict]:
    """Fetch Yahoo's published men's bracket for a tournament year.

    Returns:
        (bracket_json, metadata) where bracket_json matches the optimizer's
        ``{"regions": [{"name": ..., "teams": {"1": "Team", ...}}, ...]}``
        format and metadata contains source details like the Yahoo game key.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    payload, resolved_game_key, fantasy_game = _discover_yahoo_bracket_payload(
        session=session,
        year=year,
        game_key=game_key,
    )
    bracket = _parse_yahoo_bracket(payload, ratings or {})

    metadata = {
        "source": "yahoo",
        "game_key": resolved_game_key,
        "season": int(fantasy_game["season"]),
        "title": fantasy_game.get("name", ""),
        "url": fantasy_game.get("url", ""),
    }

    if save:
        os.makedirs(DATA_DIR, exist_ok=True)
        raw_path = os.path.join(DATA_DIR, f"yahoo_bracket_{year}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        bracket_path = os.path.join(DATA_DIR, f"bracket_{year}.json")
        with open(bracket_path, "w", encoding="utf-8") as f:
            json.dump(bracket, f, indent=2)

    return bracket, metadata


def _discover_yahoo_bracket_payload(session: requests.Session,
                                    year: int,
                                    game_key: int | None) -> tuple[dict, int, dict]:
    """Find the Yahoo men's bracket payload for a season."""
    last_error: Exception | None = None
    for candidate in _candidate_game_keys(year, game_key):
        try:
            payload = _fetch_yahoo_payload(session, candidate)
            fantasy_game = payload.get("data", {}).get("fantasyGame")
            if not fantasy_game:
                continue
            if not _is_matching_mens_bracket(fantasy_game, year):
                continue
            return payload, candidate, fantasy_game
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc

    if game_key is not None:
        raise RuntimeError(f"Yahoo bracket gameKey={game_key} did not match season {year}")
    if last_error is not None:
        raise RuntimeError(f"Could not fetch Yahoo bracket for {year}: {last_error}") from last_error
    raise RuntimeError(f"Could not find Yahoo men's bracket for {year}")


def _candidate_game_keys(year: int, explicit_game_key: int | None) -> Iterable[int]:
    """Yield likely Yahoo game keys for a tournament year."""
    seen: set[int] = set()

    def emit(candidate: int):
        if candidate > 0 and candidate not in seen:
            seen.add(candidate)
            return candidate
        return None

    if explicit_game_key is not None:
        candidate = emit(int(explicit_game_key))
        if candidate is not None:
            yield candidate

    if year in KNOWN_YAHOO_MENS_GAME_KEYS:
        candidate = emit(KNOWN_YAHOO_MENS_GAME_KEYS[year])
        if candidate is not None:
            yield candidate

    nearest_year = min(
        KNOWN_YAHOO_MENS_GAME_KEYS,
        key=lambda known_year: abs(known_year - year),
    )
    estimated = KNOWN_YAHOO_MENS_GAME_KEYS[nearest_year] + (year - nearest_year) * 10

    for distance in range(0, 41):
        for candidate_value in (estimated + distance, estimated - distance):
            candidate = emit(candidate_value)
            if candidate is not None:
                yield candidate

    low = min(KNOWN_YAHOO_MENS_GAME_KEYS.values()) - 100
    high = max(KNOWN_YAHOO_MENS_GAME_KEYS.values()) + 100
    for candidate_value in range(max(1, low), high + 1):
        candidate = emit(candidate_value)
        if candidate is not None:
            yield candidate


def _fetch_yahoo_payload(session: requests.Session, game_key: int) -> dict:
    """Download one Yahoo bracket response and decode the JSON body."""
    response = session.get(YAHOO_BRACKET_URL.format(game_key=game_key), timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in (None, "OK"):
        raise RuntimeError(f"Yahoo returned status={payload.get('status')!r} for gameKey={game_key}")
    return payload


def _is_matching_mens_bracket(fantasy_game: dict, year: int) -> bool:
    """Check whether a Yahoo response is the men's bracket for the requested year."""
    season = fantasy_game.get("season")
    url = (fantasy_game.get("url") or "").lower()
    title = (fantasy_game.get("name") or "").lower()
    return season == year and (
        YAHOO_MENS_URL_FRAGMENT in url or bool(re.search(r"\bmen'?s\b", title))
    )


def _parse_yahoo_bracket(payload: dict, ratings: dict[str, dict]) -> dict:
    """Convert Yahoo's bracket payload into the optimizer's JSON format."""
    fantasy_game = payload.get("data", {}).get("fantasyGame")
    if not fantasy_game:
        raise ValueError("Yahoo payload did not include data.fantasyGame")

    tournament = fantasy_game.get("tournament") or {}
    round_one_slots = [
        slot for slot in (tournament.get("slots") or [])
        if str(slot.get("roundId")) == "1"
    ]
    if len(round_one_slots) != 32:
        raise ValueError(f"Expected 32 first-round slots, found {len(round_one_slots)}")
    slot_team_keys = {
        str(key)
        for slot in round_one_slots
        for key in (
            (slot.get("editorialGame") or {}).get("bracketTopTeam", {}).get("editorialTeamKey"),
            (slot.get("editorialGame") or {}).get("bracketBottomTeam", {}).get("editorialTeamKey"),
        )
        if key
    }

    resolver = _build_name_resolver(ratings)
    teams_by_key = _build_team_lookup(
        tournament=tournament,
        resolver=resolver,
        ratings=ratings,
        slot_team_keys=slot_team_keys,
    )

    slots_by_region: dict[str, list[dict]] = {}
    for slot in round_one_slots:
        region_id = str(slot.get("regionId"))
        slots_by_region.setdefault(region_id, []).append(slot)

    regions = []
    raw_regions = [
        region for region in (tournament.get("regions") or [])
        if str(region.get("regionId")) != "0"
    ]
    raw_regions.sort(key=lambda region: REGION_POSITION_ORDER.get(region.get("position"), 99))

    for region in raw_regions:
        region_id = str(region["regionId"])
        teams = _parse_region_teams(
            region_name=region["name"],
            slots=slots_by_region.get(region_id, []),
            teams_by_key=teams_by_key,
        )
        regions.append({
            "name": region["name"],
            "teams": {str(seed): name for seed, name in teams.items()},
        })

    if len(regions) != 4:
        raise ValueError(f"Expected 4 tournament regions, found {len(regions)}")

    return {"regions": regions}


def _build_team_lookup(tournament: dict,
                       resolver,
                       ratings: dict[str, dict],
                       slot_team_keys: set[str]) -> dict[str, dict]:
    """Map Yahoo editorial team keys to normalized display names and seeds."""
    raw_entries: dict[str, dict] = {}
    for item in tournament.get("tournamentTeams") or []:
        key = item.get("editorialTeamKey")
        editorial_team = item.get("editorialTeam") or {}
        display_name = editorial_team.get("displayName")
        abbreviation = editorial_team.get("abbreviation") or display_name or ""
        seed = item.get("seed")
        if not key or not display_name or seed is None:
            continue
        raw_entries[str(key)] = {
            "seed": int(seed),
            "name": resolver(display_name),
            "display_name": display_name,
            "abbreviation": abbreviation,
            "is_placeholder": _is_play_in_placeholder(display_name, abbreviation),
            "wins": int((item.get("stats") or {}).get("wins") or 0),
            "losses": int((item.get("stats") or {}).get("losses") or 0),
        }

    teams_by_key: dict[str, dict] = {}
    for key, entry in raw_entries.items():
        if entry["is_placeholder"] and key in slot_team_keys:
            resolved_name = _resolve_placeholder_team(
                placeholder=entry,
                raw_entries=raw_entries,
                ratings=ratings,
                slot_team_keys=slot_team_keys,
            )
            teams_by_key[key] = {"seed": entry["seed"], "name": resolved_name}
        else:
            teams_by_key[key] = {"seed": entry["seed"], "name": entry["name"]}

    return teams_by_key


def _resolve_placeholder_team(placeholder: dict,
                              raw_entries: dict[str, dict],
                              ratings: dict[str, dict],
                              slot_team_keys: set[str]) -> str:
    """Collapse a First Four placeholder like 'PV/LEH' to one team name."""
    segments = [
        _normalize_code(part)
        for part in re.split(r"/+", placeholder.get("abbreviation") or placeholder.get("display_name") or "")
        if part
    ]
    candidates = []
    for key, entry in raw_entries.items():
        if key in slot_team_keys:
            continue
        if entry["seed"] != placeholder["seed"]:
            continue
        if entry["is_placeholder"]:
            continue
        codes = _build_matching_codes(entry["display_name"], entry["abbreviation"])
        if any(_codes_match(segment, codes) for segment in segments):
            candidates.append(entry)

    if not candidates:
        return placeholder["name"]

    winner = max(
        candidates,
        key=lambda entry: (_team_strength(entry, ratings), entry["name"]),
    )
    return winner["name"]


def _is_play_in_placeholder(display_name: str, abbreviation: str) -> bool:
    """Return True when Yahoo represents a play-in line as a slash placeholder."""
    return "/" in (display_name or "") or "/" in (abbreviation or "")


def _build_matching_codes(display_name: str, abbreviation: str) -> set[str]:
    """Build abbreviation variants for matching Yahoo play-in placeholders."""
    normalized_name = _normalize_code(display_name)
    normalized_abbr = _normalize_code(abbreviation)
    tokens = [_normalize_code(token) for token in re.split(r"[^A-Za-z0-9]+", display_name or "") if token]
    initials = "".join(token[:1] for token in tokens)
    consonants = re.sub(r"[AEIOU]", "", normalized_name)

    codes = {
        normalized_name,
        normalized_abbr,
        initials,
        consonants,
    }
    for text in (normalized_name, normalized_abbr, consonants):
        if len(text) >= 2:
            codes.add(text[:2])
        if len(text) >= 3:
            codes.add(text[:3])
        if len(text) >= 4:
            codes.add(text[:4])
    return {code for code in codes if code}


def _codes_match(segment: str, codes: set[str]) -> bool:
    """Match a placeholder segment like 'TX' against team abbreviation variants."""
    if segment in codes:
        return True
    return any(code.startswith(segment) or segment.startswith(code) for code in codes)


def _team_strength(entry: dict, ratings: dict[str, dict]) -> float:
    """Look up a team's strength for First Four placeholder resolution."""
    rating = float(ratings.get(entry["name"], {}).get("rating", 0.0))
    if rating > 0:
        return rating

    wins = int(entry.get("wins") or 0)
    losses = int(entry.get("losses") or 0)
    games = wins + losses
    return wins / games if games else 0.0


def _normalize_code(text: str) -> str:
    """Normalize abbreviations like 'M-OH' into 'MOH'."""
    return re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())


def _parse_region_teams(region_name: str,
                        slots: list[dict],
                        teams_by_key: dict[str, dict]) -> dict[int, str]:
    """Build a {seed: team_name} mapping for one region."""
    if len(slots) != 8:
        raise ValueError(f"Expected 8 first-round slots for {region_name}, found {len(slots)}")

    teams: dict[int, str] = {}
    for slot in sorted(slots, key=_slot_sort_key):
        editorial_game = slot.get("editorialGame") or {}
        top_key = (editorial_game.get("bracketTopTeam") or {}).get("editorialTeamKey")
        bottom_key = (editorial_game.get("bracketBottomTeam") or {}).get("editorialTeamKey")
        if top_key not in teams_by_key or bottom_key not in teams_by_key:
            raise ValueError(f"Missing team metadata for {region_name} slot {slot.get('slotId')}")

        top_team = teams_by_key[top_key]
        bottom_team = teams_by_key[bottom_key]
        teams[top_team["seed"]] = top_team["name"]
        teams[bottom_team["seed"]] = bottom_team["name"]

    if len(teams) != 16:
        raise ValueError(f"Expected 16 seeded teams for {region_name}, found {len(teams)}")

    return teams


def _slot_sort_key(slot: dict) -> int:
    """Sort Yahoo slotIds like '1_8', '1_9', ... in bracket order."""
    slot_id = str(slot.get("slotId") or "")
    suffix = slot_id.split("_")[-1]
    try:
        return int(suffix)
    except ValueError:
        return 999


def _build_name_resolver(ratings: dict[str, dict]):
    """Create a best-effort public-name to canonical-name resolver."""
    aliases_path = os.path.join(os.path.dirname(__file__), "..", "data", "team_aliases.json")
    aliases: dict[str, str] = {}
    if os.path.exists(aliases_path):
        with open(aliases_path, "r", encoding="utf-8") as f:
            aliases = json.load(f)

    normalized_to_canonical: dict[str, str] = {}
    for canonical in ratings:
        normalized_to_canonical[_normalize_name(canonical)] = canonical

    for alias, canonical in aliases.items():
        if alias.startswith("_comment"):
            continue
        normalized_to_canonical[_normalize_name(alias)] = canonical
        normalized_to_canonical.setdefault(_normalize_name(canonical), canonical)

    def resolve(name: str) -> str:
        return normalized_to_canonical.get(_normalize_name(name), name)

    return resolve


def _normalize_name(text: str) -> str:
    """Normalize team names for alias lookups."""
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
    return text.strip().lower()
