from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CBS_LEADERBOARD_URL = "https://www.cbssports.com/golf/leaderboard/pga-tour/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_FILE = BASE_DIR / "data" / "leaderboard.json"


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fetch_html(url: str, timeout: int = 20) -> str:
    """
    Try requests first. If a sports site blocks the cloud runner or returns
    incomplete HTML, use Playwright/Chromium when available.
    """
    request_error = None
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        html = response.text
        if "leaderboard" in html.lower() and len(html) > 5000:
            return html
    except Exception as exc:
        request_error = exc

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        if request_error:
            raise RuntimeError(
                f"requests failed ({request_error}) and Playwright is not installed"
            ) from exc
        raise

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1800)
        html = page.content()
        browser.close()

    if len(html) < 5000:
        raise RuntimeError("CBS leaderboard page returned unexpectedly short HTML")

    return html


def fetch_cbs_leaderboard(timeout: int = 20) -> dict:
    html = fetch_html(CBS_LEADERBOARD_URL, timeout=timeout)
    return parse_cbs_leaderboard(html)


def _full_player_name(cell) -> str:
    """
    CBS can render both abbreviation and full name in the same table cell,
    e.g. 'G. Woodland Gary Woodland'. Prefer the longest link/text candidate.
    """
    candidates = [clean(a.get_text(" ", strip=True)) for a in cell.find_all("a")]
    candidates += [clean(x) for x in cell.stripped_strings]
    candidates = [x for x in candidates if x and not re.fullmatch(r"[A-Z]{2,3}", x)]

    if candidates:
        return max(candidates, key=len)

    return clean(cell.get_text(" ", strip=True))


def parse_cbs_leaderboard(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    match = re.search(r"^\d{4}\s+(.+?)\s+Leaderboard\b", title, re.I)
    tournament = clean(match.group(1)) if match else ""

    table = None
    for candidate in soup.find_all("table"):
        header = clean(
            " ".join(x.get_text(" ", strip=True) for x in candidate.find_all("th"))
        ).lower()
        if "pos" in header and "name" in header and "thru" in header:
            table = candidate
            break

    if table is None:
        raise RuntimeError("CBS leaderboard table was not found")

    rows = []

    for tr in table.find_all("tr"):
        cell_nodes = tr.find_all(["th", "td"])
        cells = [clean(x.get_text(" ", strip=True)) for x in cell_nodes]

        if len(cells) < 6:
            continue

        low = [c.lower() for c in cells]
        if "pos" in low or ("name" in low and "thru" in low):
            continue

        # CBS current layout:
        # pos | country | player | to par | thru | today | r1 | r2 | r3 | r4 | total
        position = cells[0]
        player = _full_player_name(cell_nodes[2]) if len(cell_nodes) > 2 else ""
        to_par = cells[3] if len(cells) > 3 else ""
        thru = cells[4] if len(cells) > 4 else ""
        today = cells[5] if len(cells) > 5 else ""

        if not player:
            continue

        rows.append({
            "position": position,
            "player": player,
            "toPar": to_par,
            "thru": thru,
            "today": today,
            "r1": cells[6] if len(cells) > 6 else "",
            "r2": cells[7] if len(cells) > 7 else "",
            "r3": cells[8] if len(cells) > 8 else "",
            "r4": cells[9] if len(cells) > 9 else "",
            "total": cells[10] if len(cells) > 10 else "",
        })

    if len(rows) < 15:
        raise RuntimeError(f"CBS leaderboard parsed only {len(rows)} player rows")

    payload = {
        "tournament": tournament,
        "source": CBS_LEADERBOARD_URL,
        "updatedUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "players": rows[:15],
    }
    return payload


def save_snapshot(payload: dict, path: Path = SNAPSHOT_FILE) -> None:
    if len(payload.get("players", [])) < 15:
        raise RuntimeError("Refusing to save leaderboard with fewer than 15 players")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path = SNAPSHOT_FILE) -> dict:
    if not path.exists():
        return {
            "tournament": "",
            "source": CBS_LEADERBOARD_URL,
            "updatedUtc": None,
            "players": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_snapshot() -> dict:
    payload = fetch_cbs_leaderboard()
    save_snapshot(payload)
    return payload


if __name__ == "__main__":
    payload = refresh_snapshot()
    print(
        f"Saved {len(payload['players'])} leaderboard rows "
        f"for {payload.get('tournament') or 'current PGA TOUR event'}"
    )
