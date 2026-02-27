"""ESPN/Yahoo public pick percentage scraper.

Scrapes the percentage of public brackets that pick each team to advance
to each round. This data is only available after brackets open (~March 16).
"""

import json
import os
import requests
from bs4 import BeautifulSoup

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")

ESPN_PICKS_URL = "https://fantasy.espn.com/games/tournament-challenge-bracket/en/whopickedwhom"
YAHOO_PICKS_URL = "https://tournament.fantasysports.yahoo.com/mens-basketball-bracket/pickdistribution"


def fetch_espn_picks(save: bool = True) -> dict[str, dict[int, float]]:
    """Scrape ESPN Tournament Challenge pick percentages.

    Returns:
        {team_name: {round: pick_fraction}} where fraction is 0-1
    """
    print(f"Fetching ESPN pick percentages from {ESPN_PICKS_URL}...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(ESPN_PICKS_URL, headers=headers, timeout=30)
        resp.raise_for_status()

        if save:
            os.makedirs(DATA_DIR, exist_ok=True)
            path = os.path.join(DATA_DIR, "espn_picks.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(resp.text)

        return _parse_espn_picks(resp.text)

    except requests.RequestException as e:
        print(f"Warning: Could not fetch ESPN picks: {e}")
        print("This data is only available during the tournament (mid-March).")
        print("Use --manual flag to load from a CSV file instead.")
        return {}


def fetch_yahoo_picks(save: bool = True) -> dict[str, dict[int, float]]:
    """Scrape Yahoo Bracket pick distribution.

    Returns:
        {team_name: {round: pick_fraction}} where fraction is 0-1
    """
    print(f"Fetching Yahoo pick percentages from {YAHOO_PICKS_URL}...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(YAHOO_PICKS_URL, headers=headers, timeout=30)
        resp.raise_for_status()

        if save:
            os.makedirs(DATA_DIR, exist_ok=True)
            path = os.path.join(DATA_DIR, "yahoo_picks.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(resp.text)

        return _parse_yahoo_picks(resp.text)

    except requests.RequestException as e:
        print(f"Warning: Could not fetch Yahoo picks: {e}")
        print("This data is only available during the tournament (mid-March).")
        print("Use --manual flag to load from a CSV file instead.")
        return {}


def _parse_espn_picks(html: str) -> dict[str, dict[int, float]]:
    """Parse ESPN Who Picked Whom page.

    ESPN typically shows each team with percentages for each round they could reach.
    The exact layout changes yearly, so this is best-effort parsing.
    """
    soup = BeautifulSoup(html, "html.parser")
    picks = {}

    # ESPN's "Who Picked Whom" page typically has team rows with round percentages
    # Look for table-like structures
    rows = soup.select("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        # Try to find team name and percentages
        name = None
        for cell in cells:
            link = cell.find("a")
            text = (link or cell).get_text(strip=True)
            if text and not text.replace(".", "").replace("%", "").isdigit():
                name = text
                break

        if not name:
            continue

        pcts = {}
        round_num = 1
        for cell in cells:
            text = cell.get_text(strip=True).replace("%", "")
            try:
                val = float(text)
                if 0 <= val <= 100:
                    pcts[round_num] = val / 100.0
                    round_num += 1
            except ValueError:
                continue

        if pcts:
            picks[name] = pcts

    if not picks:
        print("Warning: Could not parse ESPN pick data. Page may require JavaScript rendering.")
        print("Try loading manually with: python cli.py fetch-picks --manual picks.csv")

    return picks


def _parse_yahoo_picks(html: str) -> dict[str, dict[int, float]]:
    """Parse Yahoo pick distribution page."""
    soup = BeautifulSoup(html, "html.parser")
    picks = {}

    rows = soup.select("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        name = None
        for cell in cells:
            text = cell.get_text(strip=True)
            if text and not text.replace(".", "").replace("%", "").isdigit():
                name = text
                break

        if not name:
            continue

        pcts = {}
        round_num = 1
        for cell in cells:
            text = cell.get_text(strip=True).replace("%", "")
            try:
                val = float(text)
                if 0 <= val <= 100:
                    pcts[round_num] = val / 100.0
                    round_num += 1
            except ValueError:
                continue

        if pcts:
            picks[name] = pcts

    if not picks:
        print("Warning: Could not parse Yahoo pick data.")

    return picks


def save_picks(picks: dict[str, dict[int, float]], filepath: str):
    """Save parsed pick percentages to JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    # Convert int keys to strings for JSON
    serializable = {name: {str(r): p for r, p in rpcts.items()} for name, rpcts in picks.items()}
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)
    print(f"Saved pick percentages to {filepath}")


def load_picks(filepath: str) -> dict[str, dict[int, float]]:
    """Load previously saved pick percentages from JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {name: {int(r): p for r, p in rpcts.items()} for name, rpcts in data.items()}
