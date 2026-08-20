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

    rows = []

    # CBS can render the table header and body in separate table elements.
    # Scan every rendered <tr> globally instead of restricting to one table.
    for tr in soup.find_all("tr"):
        cell_nodes = tr.find_all(["th", "td"])
        if len(cell_nodes) < 6:
            continue

        cells = [clean(x.get_text(" ", strip=True)) for x in cell_nodes]
        low = [c.lower() for c in cells]

        # Header row.
        if "pos" in low or ("name" in low and "thru" in low):
            continue

        position = cells[0]

        # Valid positions are things like 1, 12, T2, T9, etc.
        if not re.fullmatch(r"T?\d+", position, re.I):
            continue

        # CBS normally renders:
        # pos | country | player | to par | thru | today | ...
        # The country cell may be image-only, so keep index positions based on
        # actual td elements rather than text presence.
        if len(cell_nodes) >= 6:
            player_index = 2 if len(cell_nodes) >= 7 else 1
            score_index = player_index + 1
            thru_index = player_index + 2
            today_index = player_index + 3
        else:
            continue

        if today_index >= len(cells):
            continue

        player = _full_player_name(cell_nodes[player_index])
        if not player:
            continue

        # Guard against accidentally parsing unrelated site tables.
        to_par = cells[score_index]
        thru = cells[thru_index]
        today = cells[today_index]

        if not (
            re.fullmatch(r"(?:E|[+-]?\d+|-)", to_par, re.I)
            and re.fullmatch(r"(?:F|\d+|-)", thru, re.I)
        ):
            continue

        rows.append({
            "position": position,
            "player": player,
            "toPar": to_par,
            "thru": thru,
            "today": today,
            "r1": cells[today_index + 1] if len(cells) > today_index + 1 else "",
            "r2": cells[today_index + 2] if len(cells) > today_index + 2 else "",
            "r3": cells[today_index + 3] if len(cells) > today_index + 3 else "",
            "r4": cells[today_index + 4] if len(cells) > today_index + 4 else "",
            "total": cells[today_index + 5] if len(cells) > today_index + 5 else "",
        })

    # Text fallback for CBS layout changes where semantic table rows disappear.
    if len(rows) < 15:
        body_text = clean(soup.get_text(" | ", strip=True))
        marker = re.search(
            r"pos\s*\|\s*ctry\s*\|\s*name\s*\|\s*to par\s*\|\s*thru\s*\|\s*today",
            body_text,
            re.I,
        )
        if marker:
            segment = body_text[marker.end():]
            # Find row starts such as "| 1 |", "| T2 |".
            row_starts = list(re.finditer(r"\|\s*(T?\d+)\s*\|", segment, re.I))
            text_rows = []

            for i, rm in enumerate(row_starts):
                end_pos = row_starts[i + 1].start() if i + 1 < len(row_starts) else len(segment)
                chunk = segment[rm.end():end_pos]
                parts = [clean(p) for p in chunk.split("|") if clean(p)]

                # Expected text columns after position:
                # country, player, to-par, thru, today, r1...
                if len(parts) < 5:
                    continue

                country = parts[0]
                player = parts[1]
                to_par = parts[2]
                thru = parts[3]
                today = parts[4]

                # Player text may be "G. Woodland Gary Woodland".
                # Prefer the final full-name portion when available.
                mname = re.search(
                    r"(?:[A-Z]\.\s+[A-Za-zÀ-ÿ' -]+\s+)?([A-Z][A-Za-zÀ-ÿ' -]+\s+[A-Z][A-Za-zÀ-ÿ' -]+)$",
                    player
                )
                if mname:
                    player = clean(mname.group(1))

                if not re.fullmatch(r"(?:E|[+-]?\d+|-)", to_par, re.I):
                    continue
                if not re.fullmatch(r"(?:F|\d+|-)", thru, re.I):
                    continue

                text_rows.append({
                    "position": rm.group(1).upper(),
                    "player": player,
                    "toPar": to_par,
                    "thru": thru,
                    "today": today,
                    "r1": parts[5] if len(parts) > 5 else "",
                    "r2": parts[6] if len(parts) > 6 else "",
                    "r3": parts[7] if len(parts) > 7 else "",
                    "r4": parts[8] if len(parts) > 8 else "",
                    "total": parts[9] if len(parts) > 9 else "",
                })

            if len(text_rows) > len(rows):
                rows = text_rows

    if len(rows) < 15:
        raise RuntimeError(f"CBS leaderboard parsed only {len(rows)} player rows")

    return {
        "tournament": tournament,
        "source": CBS_LEADERBOARD_URL,
        "updatedUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "players": rows[:15],
    }



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
