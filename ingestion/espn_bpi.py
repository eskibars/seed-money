"""ESPN BPI (Basketball Power Index) scraper.

Scrapes team BPI ratings from ESPN's college basketball BPI page.
Free, no auth required, but layout may change between seasons.
"""

import os
import requests
from bs4 import BeautifulSoup

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")

BPI_URL = "https://www.espn.com/mens-college-basketball/bpi"


def fetch_espn_bpi(save: bool = True) -> dict[str, dict]:
    """Scrape ESPN BPI ratings.

    Returns:
        {team_name: {"bpi": float, "rank": int}}
    """
    print(f"Fetching ESPN BPI from {BPI_URL}...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    resp = requests.get(BPI_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    if save:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "espn_bpi.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(resp.text)

    return _parse_bpi_html(resp.text)


def _parse_bpi_html(html: str) -> dict[str, dict]:
    """Parse BPI ratings from ESPN HTML.

    ESPN's BPI page uses JavaScript rendering, so simple HTML parsing may
    only get partial data. This attempts to extract what's available.
    """
    soup = BeautifulSoup(html, "html.parser")
    ratings = {}

    # ESPN table rows typically have class patterns like "Table__TR"
    rows = soup.select("tr.Table__TR")
    if not rows:
        rows = soup.find_all("tr")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Try to extract team name and BPI score
        # ESPN layout: rank | team | BPI | ...
        team_cell = cells[0] if len(cells) > 0 else None
        if team_cell:
            # Team name might be in an anchor tag
            link = team_cell.find("a")
            name = link.get_text(strip=True) if link else team_cell.get_text(strip=True)
            if not name or name.isdigit():
                # This might be the rank column, try next cell
                if len(cells) > 1:
                    link = cells[1].find("a")
                    name = link.get_text(strip=True) if link else cells[1].get_text(strip=True)

            if name and not name.isdigit():
                try:
                    # BPI is typically in the 3rd or 4th column
                    bpi = None
                    for cell in cells[1:]:
                        text = cell.get_text(strip=True)
                        try:
                            val = float(text)
                            if -30 < val < 50:  # BPI range is roughly -25 to 40
                                bpi = val
                                break
                        except ValueError:
                            continue

                    if bpi is not None:
                        ratings[name] = {"bpi": bpi}
                except (ValueError, IndexError):
                    continue

    if not ratings:
        print("Warning: Could not parse ESPN BPI data. The page may require JavaScript.")
        print("Consider using Torvik ratings instead, or enter data manually.")

    return ratings


def bpi_to_rating(bpi: float, bpi_min: float = -25.0, bpi_max: float = 40.0) -> float:
    """Convert BPI score to a 0-1 rating scale.

    Simple linear normalization. Not as principled as Barthag but
    allows combining BPI with other rating systems.
    """
    return max(0.01, min(0.99, (bpi - bpi_min) / (bpi_max - bpi_min)))
