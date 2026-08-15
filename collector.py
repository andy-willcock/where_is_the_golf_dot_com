from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


LOG = logging.getLogger("pga_collector")
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "data" / "schedule.json"

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Sources are deliberately ranked. A detailed viewing guide is more useful than
# a generic schedule page because it contains start/end windows.
SOURCE_PRIORITY = {
    "pgatour.com": 100,
    "golfchannel.com": 95,
    "nbcsports.com": 90,
    "cbssports.com": 85,
    "espn.com": 80,
}

STREAMING_PROVIDERS = {
    "ESPN+",
    "Paramount+",
    "Peacock",
    "CBS Sports App",
    "CBSSports.com",
    "NBC Sports App",
    "GolfChannel.com",
    "PGA TOUR LIVE",
    "PGA Tour Live",
}

TV_PROVIDERS = {
    "CBS",
    "NBC",
    "ESPN",
    "ESPN2",
    "Golf Channel",
    "USA Network",
    "NBCSN",
    "NBC Sports Network",
}

ROUND_BY_WEEKDAY = {
    3: "Round 1",       # Thursday
    4: "Round 2",       # Friday
    5: "Round 3",       # Saturday
    6: "Final Round",   # Sunday
}

DAY_NAME_TO_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

TIME_TOKEN = r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|AM|PM)?"
TIME_RANGE_RE = re.compile(
    rf"(?P<start>{TIME_TOKEN})\s*(?:-|–|—|to)\s*(?P<end>{TIME_TOKEN})",
    re.I,
)

DAY_HEADING_RE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
    r"(?:,\s*(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2})?",
    re.I,
)

ROUND_HEADING_RE = re.compile(r"\bRound\s+([1-4])\b|\bFinal Round\b", re.I)

COVERAGE_KEYWORDS = (
    "coverage",
    "pga tour live",
    "television",
    "streaming",
    "tv",
    "watch",
)


@dataclass
class Tournament:
    name: str
    start_date: date
    end_date: date
    course: str = ""
    location: str = ""
    source_url: str = ""


@dataclass
class Coverage:
    round: str
    provider: str
    feed: str
    type: str
    startUtc: str
    endUtc: str
    sourceUrl: str
    sourceDomain: str
    sourceLabel: str

    @property
    def dedupe_key(self):
        return (
            self.round.lower(),
            self.provider.lower(),
            self.startUtc,
            self.endUtc,
        )


class CollectionError(RuntimeError):
    pass


def get(url: str, timeout: int = 20) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def fetch_html(url: str, use_browser_fallback: bool = True) -> str:
    """
    Fetch a page with requests first. If blocked and Playwright is installed,
    retry in a real Chromium browser.

    Install browser fallback with:
        pip install playwright
        playwright install chromium
    """
    try:
        return get(url).text
    except Exception as request_error:
        LOG.warning("requests failed for %s: %s", url, request_error)

    if not use_browser_fallback:
        raise CollectionError(f"Could not fetch {url}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CollectionError(
            f"Could not fetch {url}. requests was blocked and Playwright is not installed."
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1200)
            html = page.content()
            browser.close()
            return html
    except Exception as browser_error:
        raise CollectionError(
            f"Both requests and browser fallback failed for {url}: {browser_error}"
        ) from browser_error


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    # Preserve article-like block boundaries.
    blocks = []
    selectors = "h1,h2,h3,h4,p,li,div"
    for node in soup.select(selectors):
        text = clean_text(node.get_text(" ", strip=True))
        if text and len(text) < 600:
            blocks.append(text)

    # Deduplicate adjacent DOM nesting repetitions.
    deduped = []
    seen_recent = set()
    for line in blocks:
        if line in seen_recent:
            continue
        deduped.append(line)
        seen_recent.add(line)
        if len(seen_recent) > 40:
            seen_recent = set(deduped[-20:])

    return deduped



def discover_current_tournament(now: datetime | None = None) -> Tournament:
    """
    Identify the active PGA TOUR event without relying on ESPN.

    Order:
      1. CBS Sports PGA Tour schedule
      2. PGA TOUR official schedule
      3. search-result fallback from reputable sources
    """
    now = now or datetime.now(UTC)
    today = now.astimezone(EASTERN).date()
    errors = []

    schedule_sources = [
        ("https://www.cbssports.com/golf/schedules/", parse_cbs_schedule),
        ("https://www.pgatour.com/schedule", parse_pgatour_schedule),
    ]

    for url, parser_fn in schedule_sources:
        try:
            html = fetch_html(url)
            tournaments = parser_fn(html, today.year, url)
            LOG.warning(
                "Tournament discovery: parsed %d events from %s",
                len(tournaments),
                url,
            )

            active = choose_active_tournament(tournaments, today)
            if active:
                LOG.warning(
                    "Tournament discovery: %s -> %s (%s to %s)",
                    urlparse(url).netloc,
                    active.name,
                    active.start_date,
                    active.end_date,
                )
                return active
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            LOG.exception("Tournament discovery failed at %s", url)

    if DDGS is not None:
        try:
            fallback = discover_tournament_from_search(today)
            if fallback:
                LOG.warning(
                    "Tournament discovery: search fallback -> %s (%s to %s)",
                    fallback.name,
                    fallback.start_date,
                    fallback.end_date,
                )
                return fallback
        except Exception as exc:
            errors.append(f"search fallback: {exc}")
            LOG.exception("Tournament search fallback failed")

    raise CollectionError(
        "Could not identify the current PGA TOUR tournament. "
        + " | ".join(errors)
    )



def discover_tournament_from_search(today: date) -> Tournament | None:
    """
    Last-resort tournament discovery from reputable search-result snippets.

    Example snippet:
      "Aug 13 - 16. FedEx St. Jude Championship. TPC Southwind - Memphis, TN."
    """
    if DDGS is None:
        return None

    queries = [
        f"PGA TOUR schedule {today.year} {today.strftime('%B')} ESPN",
        f"PGA TOUR schedule {today.year} {today.strftime('%B')} pgatour",
        f"PGA Tour schedule {today.year} CBS Sports",
    ]

    domain_allowlist = (
        "pgatour.com",
        "cbssports.com",
        "sportsmediawatch.com",
    )

    date_pattern = re.compile(
        r"\b("
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        r")\.?\s+(\d{1,2})\s*[-–—]\s*"
        r"(?:(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+)?"
        r"(\d{1,2})",
        re.I,
    )

    month_lookup = {
        "jan": 1, "january": 1, "feb": 2, "february": 2,
        "mar": 3, "march": 3, "apr": 4, "april": 4,
        "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    candidates = []

    with DDGS() as ddgs:
        for query in queries:
            for result in ddgs.text(
                query,
                region="us-en",
                safesearch="moderate",
                max_results=10,
            ):
                url = result.get("href") or result.get("url") or ""
                domain = urlparse(url).netloc.lower().removeprefix("www.")

                if not any(domain.endswith(d) for d in domain_allowlist):
                    continue

                combined = clean_text(
                    " ".join(
                        [
                            result.get("title", ""),
                            result.get("body", ""),
                        ]
                    )
                )

                matches = list(date_pattern.finditer(combined))
                for i, match in enumerate(matches):
                    sm, sd, em, ed = match.groups()
                    sm_num = month_lookup[sm.lower().rstrip(".")]
                    em_num = month_lookup[(em or sm).lower().rstrip(".")]

                    try:
                        start_date = date(today.year, sm_num, int(sd))
                        end_date = date(today.year, em_num, int(ed))
                    except ValueError:
                        continue

                    # Only keep the event relevant to today / this week.
                    if not (
                        start_date <= today <= end_date
                        or today < start_date <= today + timedelta(days=6)
                    ):
                        continue

                    next_start = matches[i + 1].start() if i + 1 < len(matches) else len(combined)
                    tail = clean_text(combined[match.end():next_start]).strip(" .-–—|")

                    # Remove obvious schedule-page title/header prefixes.
                    tail = re.sub(
                        r"^(?:PGA TOUR Schedule|PGA Tour Schedule|Schedule)\s*[-–—:]?\s*",
                        "",
                        tail,
                        flags=re.I,
                    )

                    # Event names usually end in one of these tournament terms.
                    event_match = re.match(
                        r"(.+?\b(?:Championship|Classic|Invitational|Open|Cup|"
                        r"Pro-Am|Masters|PLAYERS)\b)",
                        tail,
                        flags=re.I,
                    )

                    if not event_match:
                        continue

                    name = clean_text(event_match.group(1)).strip(" .")
                    if len(name) < 4:
                        continue

                    candidates.append(
                        Tournament(
                            name=name,
                            start_date=start_date,
                            end_date=end_date,
                            source_url=url,
                        )
                    )

    if not candidates:
        return None

    # Active event beats upcoming event.
    active = [c for c in candidates if c.start_date <= today <= c.end_date]
    pool = active or candidates

    # Majority vote by normalized event name, then earliest start.
    counts = {}
    for c in pool:
        key = re.sub(r"[^a-z0-9]+", " ", c.name.lower()).strip()
        counts[key] = counts.get(key, 0) + 1

    pool.sort(
        key=lambda c: (
            -counts[re.sub(r"[^a-z0-9]+", " ", c.name.lower()).strip()],
            c.start_date,
        )
    )
    return pool[0]

def parse_espn_schedule(html: str, year: int, source_url: str) -> list[Tournament]:
    """
    Parse ESPN's PGA TOUR schedule.

    ESPN has used several renderings of this page:
      1. date, event and venue as separate text nodes;
      2. a single collapsed row such as
         "Aug 13 - 16 FedEx St. Jude Championship TPC Southwind - Memphis, TN".

    Parse the page's visible text as a stream instead of assuming line boundaries.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    raw_text = soup.get_text(" ", strip=True)
    text = clean_text(raw_text)

    # Match each schedule row by locating its date range and using the next
    # date range as the boundary. This is much more stable than DOM selectors.
    date_token = re.compile(
        r"\b("
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
        r")\s+(\d{1,2})\s*-\s*"
        r"(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?"
        r"(\d{1,2})\b",
        re.I,
    )

    matches = list(date_token.finditer(text))
    results = []

    for idx, match in enumerate(matches):
        start_month, start_day, end_month, end_day = match.groups()
        end_month = end_month or start_month

        try:
            start_date = datetime.strptime(
                f"{start_month} {start_day} {year}", "%b %d %Y"
            ).date()
            end_date = datetime.strptime(
                f"{end_month} {end_day} {year}", "%b %d %Y"
            ).date()
        except ValueError:
            continue

        row_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        row = clean_text(text[match.end():row_end])

        # Trim common table/header noise.
        row = re.sub(
            r"^(?:Tournament|Course|Location|Winner|Purse|Results|Tickets)\s+",
            "",
            row,
            flags=re.I,
        )

        if not row:
            continue

        # ESPN's visible row normally contains:
        # EVENT NAME + COURSE + " - " + LOCATION + optional winner/purse data.
        course = ""
        location = ""
        name = ""

        if " - " in row:
            left, right = row.split(" - ", 1)

            # Location is usually City, ST. Stop before winner / purse metadata.
            loc_match = re.match(
                r"([A-Za-z .'-]+,\s*[A-Z]{2})(?:\s|$)",
                right,
            )
            if loc_match:
                location = clean_text(loc_match.group(1))

            # Try to split event name from venue using common golf venue words.
            venue_match = re.search(
                r"\b("
                r"TPC|Golf Club|Golf Course|Country Club|GC\b|CC\b|"
                r"Club de Golf|Resort|Links|Course|Club"
                r")",
                left,
                flags=re.I,
            )

            if venue_match:
                # Walk backward to a plausible venue boundary. ESPN does not
                # consistently insert separators, so use known schedule names
                # from title-like text when possible.
                known_event_markers = [
                    "Championship",
                    "Classic",
                    "Invitational",
                    "Open",
                    "Cup",
                    "Pro-Am",
                    "Masters",
                    "PLAYERS",
                ]

                split_at = None
                for marker in known_event_markers:
                    marker_positions = [
                        m.end()
                        for m in re.finditer(
                            rf"\b{re.escape(marker)}\b",
                            left,
                            flags=re.I,
                        )
                    ]
                    if marker_positions:
                        split_at = marker_positions[-1]
                        break

                if split_at:
                    name = clean_text(left[:split_at])
                    course = clean_text(left[split_at:])
                else:
                    # Fallback: venue begins near the first recognized venue token.
                    name = clean_text(left[:venue_match.start()])
                    course = clean_text(left[venue_match.start():])
            else:
                name = clean_text(left)
        else:
            # Even if venue parsing fails, preserve the event row for active-date
            # discovery. Later sources can enrich course/location.
            name = row

        # Remove winner/purse tail that can be appended to the name.
        name = re.split(
            r"\s+\$[\d,]+|\s+(?:Final|Results|Tickets)\b",
            name,
            maxsplit=1,
            flags=re.I,
        )[0].strip()

        if len(name) < 3:
            continue

        results.append(
            Tournament(
                name=name,
                start_date=start_date,
                end_date=end_date,
                course=course,
                location=location,
                source_url=source_url,
            )
        )

    return results

def parse_cbs_schedule(html: str, year: int, source_url: str) -> list[Tournament]:
    """
    Parse CBS Sports' PGA Tour schedule table.

    CBS currently exposes rows containing:
      Aug 13-16 | FedEx St. Jude Championship | Memphis, TN | TPC Southwind | ...
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # First try table rows.
    for row in soup.find_all("tr"):
        cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
        if len(cells) < 2:
            continue

        date_text = cells[0]
        m = re.search(
            r"\b("
            r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
            r")\.?\s*(\d{1,2})\s*[-–—]\s*"
            r"(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s*)?"
            r"(\d{1,2})\b",
            date_text,
            re.I,
        )
        if not m:
            continue

        sm, sd, em, ed = m.groups()
        em = em or sm

        try:
            start_date = datetime.strptime(f"{sm} {sd} {year}", "%b %d %Y").date()
            end_date = datetime.strptime(f"{em} {ed} {year}", "%b %d %Y").date()
        except ValueError:
            continue

        name = cells[1].strip()
        location = cells[2].strip() if len(cells) > 2 else ""
        course = cells[3].strip() if len(cells) > 3 else ""

        if not name or name.lower() == "tournament":
            continue

        results.append(
            Tournament(
                name=name,
                start_date=start_date,
                end_date=end_date,
                course=course,
                location=location,
                source_url=source_url,
            )
        )

    if results:
        return results

    # Fallback for collapsed/non-table HTML.
    text = clean_text(soup.get_text(" ", strip=True))
    date_re = re.compile(
        r"\b("
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
        r")\.?\s*(\d{1,2})\s*[-–—]\s*"
        r"(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s*)?"
        r"(\d{1,2})\b",
        re.I,
    )

    matches = list(date_re.finditer(text))
    for i, m in enumerate(matches):
        sm, sd, em, ed = m.groups()
        em = em or sm

        try:
            start_date = datetime.strptime(f"{sm} {sd} {year}", "%b %d %Y").date()
            end_date = datetime.strptime(f"{em} {ed} {year}", "%b %d %Y").date()
        except ValueError:
            continue

        row_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        row = clean_text(text[m.end():row_end])

        # Event name generally ends in a tournament-type word.
        event_match = re.match(
            r"(.+?\b(?:Championship|Classic|Invitational|Open|Cup|"
            r"Pro-Am|Masters|PLAYERS)\b)",
            row,
            re.I,
        )
        if not event_match:
            continue

        name = clean_text(event_match.group(1))
        if len(name) < 4:
            continue

        results.append(
            Tournament(
                name=name,
                start_date=start_date,
                end_date=end_date,
                source_url=source_url,
            )
        )

    return results



def parse_pgatour_schedule(html: str, year: int, source_url: str) -> list[Tournament]:
    lines = html_to_lines(html)
    results = []

    date_re = re.compile(
        r"\b([A-Z]{3})\s+(\d{1,2})\s*-\s*(?:(\d{1,2})|([A-Z]{3})\s+(\d{1,2}))\b",
        re.I,
    )

    for i, line in enumerate(lines):
        m = date_re.search(line)
        if not m:
            continue

        mon, start_day, same_month_end, end_mon, end_day = m.groups()
        end_mon = end_mon or mon
        end_day = end_day or same_month_end

        if not end_day:
            continue

        try:
            start = datetime.strptime(f"{mon} {start_day} {year}", "%b %d %Y").date()
            end = datetime.strptime(f"{end_mon} {end_day} {year}", "%b %d %Y").date()
        except ValueError:
            continue

        # Try the content after the date range first.
        name = clean_text(line[m.end():]).strip(" -•")
        if not name and i + 1 < len(lines):
            name = lines[i + 1]

        if len(name) < 3:
            continue

        results.append(
            Tournament(
                name=name,
                start_date=start,
                end_date=end,
                source_url=source_url,
            )
        )

    return results


def choose_active_tournament(
    tournaments: Iterable[Tournament], today: date
) -> Optional[Tournament]:
    tournaments = list(tournaments)

    active = [t for t in tournaments if t.start_date <= today <= t.end_date]
    if active:
        # Prefer a standard four-day PGA TOUR event if multiple events appear.
        active.sort(key=lambda t: abs((t.end_date - t.start_date).days - 3))
        return active[0]

    # On Monday-Wednesday, users usually want the tournament beginning that week.
    upcoming = [
        t for t in tournaments
        if today < t.start_date <= today + timedelta(days=6)
    ]
    if upcoming:
        return sorted(upcoming, key=lambda t: t.start_date)[0]

    return None


def discover_viewing_guides(tournament: Tournament) -> list[dict]:
    if DDGS is None:
        raise CollectionError(
            "The 'ddgs' package is required for automatic viewing-guide discovery."
        )

    LOG.warning("Searching for viewing guides for: %s", tournament.name)

    year = tournament.start_date.year
    queries = [
        f'site:cbssports.com/golf/news "{tournament.name}" {year} "TV schedule"',
        f'"{tournament.name}" {year} "TV schedule" golf',
        f'"{tournament.name}" {year} "how to watch" golf',
        f'"{tournament.name}" {year} coverage ESPN Golf Channel CBS NBC',
    ]

    results = []
    seen = set()

    with DDGS() as ddgs:
        for query in queries:
            for result in ddgs.text(query, region="us-en", safesearch="moderate", max_results=10):
                url = result.get("href") or result.get("url") or ""
                if not url or url in seen:
                    continue

                domain = urlparse(url).netloc.lower().removeprefix("www.")
                if not any(domain.endswith(d) for d in SOURCE_PRIORITY):
                    continue

                seen.add(url)
                results.append(
                    {
                        "url": url,
                        "title": result.get("title", ""),
                        "snippet": result.get("body", ""),
                        "domain": domain,
                        "priority": domain_priority(domain),
                    }
                )

    results.sort(key=lambda r: r["priority"], reverse=True)
    return results


def domain_priority(domain: str) -> int:
    for known, score in SOURCE_PRIORITY.items():
        if domain.endswith(known):
            return score
    return 0


def parse_clock_token(token: str, inherited_meridiem: str | None = None) -> tuple[int, int, str]:
    token = token.strip().lower().replace(".", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", token)
    if not m:
        raise ValueError(f"Unrecognized time: {token}")

    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = m.group(3) or inherited_meridiem

    if meridiem not in ("am", "pm"):
        raise ValueError(f"Missing AM/PM information: {token}")

    if hour == 12:
        hour = 0
    if meridiem == "pm":
        hour += 12

    return hour, minute, meridiem


def meridiem_from_token(token: str) -> str | None:
    cleaned = token.lower().replace(".", "")
    if "am" in cleaned:
        return "am"
    if "pm" in cleaned:
        return "pm"
    return None


def parse_time_range(text: str, event_date: date) -> tuple[datetime, datetime] | None:
    m = TIME_RANGE_RE.search(text)
    if not m:
        return None

    start_token = m.group("start")
    end_token = m.group("end")

    start_mer = meridiem_from_token(start_token)
    end_mer = meridiem_from_token(end_token)

    # "1-3 p.m." means 1 p.m. to 3 p.m.
    if start_mer is None and end_mer is not None:
        start_mer = end_mer

    # "1 p.m.-3" is uncommon but infer the same meridiem.
    if end_mer is None and start_mer is not None:
        end_mer = start_mer

    try:
        sh, sm, _ = parse_clock_token(start_token, start_mer)
        eh, em, _ = parse_clock_token(end_token, end_mer)
    except ValueError:
        return None

    start_local = datetime(
        event_date.year, event_date.month, event_date.day, sh, sm, tzinfo=EASTERN
    )
    end_local = datetime(
        event_date.year, event_date.month, event_date.day, eh, em, tzinfo=EASTERN
    )

    # Handles a rare overnight window safely.
    if end_local <= start_local:
        end_local += timedelta(days=1)

    # Reject implausible windows instead of publishing garbage.
    duration = end_local - start_local
    if duration < timedelta(minutes=15) or duration > timedelta(hours=16):
        return None

    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def event_date_for_weekday(tournament: Tournament, weekday: int) -> date | None:
    d = tournament.start_date
    while d <= tournament.end_date:
        if d.weekday() == weekday:
            return d
        d += timedelta(days=1)
    return None


def find_heading_context(lines: list[str], index: int, tournament: Tournament) -> tuple[date | None, str | None]:
    # Search backward because many articles structure a day as H3 + several P/LI lines.
    for j in range(index, max(-1, index - 8), -1):
        line = lines[j]

        day_match = DAY_HEADING_RE.search(line)
        if day_match:
            weekday = DAY_NAME_TO_WEEKDAY[day_match.group(1).lower()]
            event_date = event_date_for_weekday(tournament, weekday)
            round_name = ROUND_BY_WEEKDAY.get(weekday, "")
            return event_date, round_name

        round_match = ROUND_HEADING_RE.search(line)
        if round_match:
            if "final" in line.lower():
                round_num = 4
            else:
                round_num = int(round_match.group(1))

            weekday = {1: 3, 2: 4, 3: 5, 4: 6}[round_num]
            event_date = event_date_for_weekday(tournament, weekday)
            return event_date, "Final Round" if round_num == 4 else f"Round {round_num}"

    return None, None


def clean_provider_piece(piece: str) -> str:
    piece = clean_text(piece)

    # Remove common affiliate/promo parentheticals.
    piece = re.sub(r"\([^)]*(?:free trial|try for free|start watching|save \$)[^)]*\)", "", piece, flags=re.I)
    piece = re.sub(r"\s+and\s+the\s+", ", ", piece, flags=re.I)
    piece = piece.strip(" .;:-")

    replacements = {
        "GOLF Channel": "Golf Channel",
        "GOLF CHANNEL": "Golf Channel",
        "PGA Tour Live": "PGA TOUR LIVE",
        "CBS Sports app": "CBS Sports App",
        "CBSSports.com": "CBSSports.com",
        "NBC Sports app": "NBC Sports App",
    }
    return replacements.get(piece, piece)


def split_providers(text: str) -> list[str]:
    # Remove descriptive tails that are not services.
    text = re.split(r"\b(?:Radio|Round starts|Tee times)\b", text, maxsplit=1, flags=re.I)[0]
    text = text.replace(" and ", ", ")
    pieces = [clean_provider_piece(p) for p in text.split(",")]
    pieces = [p for p in pieces if p]

    valid = []
    for p in pieces:
        low = p.lower()
        if any(noise in low for noise in ("fubo", "directv", "youtube tv", "hulu + live")):
            continue
        if len(p) > 45:
            continue
        valid.append(p)

    return valid


def providers_from_line(line: str, label: str) -> list[str]:
    # Most golf viewing guides use "... on Golf Channel, CBS" or "-- PGA Tour Live".
    after = ""

    on_match = re.search(r"\bon\s+(.+)$", line, re.I)
    if on_match:
        after = on_match.group(1)
    elif "--" in line:
        after = line.split("--", 1)[1]
    elif "—" in line:
        after = line.split("—", 1)[1]
    else:
        # If no "on", the label itself may be "PGA TOUR LIVE".
        if "pga tour live" in label.lower():
            after = "PGA TOUR LIVE"

    return split_providers(after)


def classify_provider(provider: str, label: str) -> str:
    if provider in STREAMING_PROVIDERS:
        return "streaming"
    if provider in TV_PROVIDERS:
        return "tv"

    low = (provider + " " + label).lower()
    if any(k in low for k in ("stream", "app", ".com", "+", "peacock", "paramount")):
        return "streaming"
    return "tv"


def label_from_line(line: str) -> str:
    if ":" in line:
        return clean_text(line.split(":", 1)[0])
    before_time = re.split(TIME_TOKEN, line, maxsplit=1, flags=re.I)[0]
    return clean_text(before_time).strip(" -–—:")


def parse_viewing_guide(
    html: str,
    tournament: Tournament,
    source_url: str,
) -> list[Coverage]:
    lines = html_to_lines(html)
    domain = urlparse(source_url).netloc.lower().removeprefix("www.")
    coverage = []

    for i, line in enumerate(lines):
        low = line.lower()

        if not any(keyword in low for keyword in COVERAGE_KEYWORDS):
            continue

        event_date, round_name = find_heading_context(lines, i, tournament)
        if event_date is None or not round_name:
            continue

        time_range = parse_time_range(line, event_date)
        if not time_range:
            continue

        label = label_from_line(line)
        providers = providers_from_line(line, label)

        # Some lines are simply "3-6 p.m. — CBS".
        if not providers:
            tail = TIME_RANGE_RE.sub("", line, count=1).strip(" -–—:;")
            providers = split_providers(tail)

        start_utc, end_utc = time_range

        for provider in providers:
            if not provider:
                continue

            ctype = classify_provider(provider, label)
            coverage.append(
                Coverage(
                    round=round_name,
                    provider=provider,
                    feed=label,
                    type=ctype,
                    startUtc=start_utc.isoformat().replace("+00:00", "Z"),
                    endUtc=end_utc.isoformat().replace("+00:00", "Z"),
                    sourceUrl=source_url,
                    sourceDomain=domain,
                    sourceLabel=label,
                )
            )

    return dedupe_coverage(coverage)


def dedupe_coverage(items: Iterable[Coverage]) -> list[Coverage]:
    best = {}
    for item in items:
        key = item.dedupe_key
        existing = best.get(key)
        if not existing:
            best[key] = item
            continue

        if domain_priority(item.sourceDomain) > domain_priority(existing.sourceDomain):
            best[key] = item

    return sorted(
        best.values(),
        key=lambda x: (x.startUtc, x.endUtc, x.provider.lower()),
    )


def score_coverage(items: list[Coverage], tournament: Tournament) -> int:
    if not items:
        return 0

    days = {c.round for c in items}
    providers = {c.provider for c in items}
    types = {c.type for c in items}

    score = len(items) * 5
    score += len(days) * 10
    score += min(len(providers), 8) * 2
    if "tv" in types:
        score += 10
    if "streaming" in types:
        score += 10
    return score


def validate_coverage(items: list[Coverage], tournament: Tournament) -> list[str]:
    issues = []

    if not items:
        return ["No complete coverage windows were parsed."]

    rounds = {c.round for c in items}
    if tournament.start_date.weekday() <= 3 and "Round 1" not in rounds:
        issues.append("Round 1 coverage was not found.")
    if "Final Round" not in rounds:
        issues.append("Final-round coverage was not found.")

    for c in items:
        start = dateparser.isoparse(c.startUtc)
        end = dateparser.isoparse(c.endUtc)

        local_date = start.astimezone(EASTERN).date()
        if not (tournament.start_date <= local_date <= tournament.end_date):
            issues.append(
                f"{c.provider} {c.startUtc} falls outside the tournament dates."
            )

        if end <= start:
            issues.append(f"{c.provider} has an invalid end time.")

    return sorted(set(issues))


def collect_current_week(
    output_path: Path = DEFAULT_OUTPUT,
    now: datetime | None = None,
    min_score: int = 45,
) -> dict:
    now = now or datetime.now(UTC)

    tournament = discover_current_tournament(now)
    LOG.warning(
        "Current tournament: %s (%s to %s)",
        tournament.name,
        tournament.start_date,
        tournament.end_date,
    )

    guides = discover_viewing_guides(tournament)
    if not guides:
        raise CollectionError("No viewing guides were discovered.")

    collected = []
    used_sources = []
    parse_failures = []

    # Parse several high-quality sources. Multiple sources help fill gaps and
    # also let us deduplicate simulcast information.
    for guide in guides[:8]:
        url = guide["url"]
        try:
            html = fetch_html(url)
            items = parse_viewing_guide(html, tournament, url)
            if items:
                LOG.warning("Viewing guide: parsed %d windows from %s", len(items), url)
                collected.extend(items)
                used_sources.append(
                    {
                        "url": url,
                        "domain": guide["domain"],
                        "title": guide.get("title", ""),
                        "parsedWindows": len(items),
                    }
                )
            else:
                parse_failures.append(f"No windows parsed: {url}")
        except Exception as exc:
            parse_failures.append(f"{url}: {exc}")
            LOG.warning("Guide failed: %s: %s", url, exc)

        # Stop once we have strong Thursday-Sunday coverage.
        current = dedupe_coverage(collected)
        if score_coverage(current, tournament) >= 100:
            rounds = {c.round for c in current}
            if {"Round 1", "Round 2", "Round 3", "Final Round"}.issubset(rounds):
                break

    collected = dedupe_coverage(collected)
    score = score_coverage(collected, tournament)
    issues = validate_coverage(collected, tournament)

    if score < min_score:
        raise CollectionError(
            "Coverage collection confidence is too low to publish.\n"
            f"Score: {score}\n"
            + "\n".join(issues + parse_failures[:5])
        )

    payload = {
        "lastUpdatedUtc": now.isoformat().replace("+00:00", "Z"),
        "tournament": {
            "name": tournament.name,
            "course": tournament.course,
            "location": tournament.location,
            "startDate": tournament.start_date.isoformat(),
            "endDate": tournament.end_date.isoformat(),
            "sourceUrl": tournament.source_url,
        },
        "coverage": [
            {
                "id": f"coverage-{i+1}",
                **asdict(item),
            }
            for i, item in enumerate(collected)
        ],
        "collection": {
            "score": score,
            "warnings": issues,
            "sources": used_sources,
            "failedSources": parse_failures,
            "sourceTimezone": "America/New_York",
            "notes": (
                "Only complete start/end windows are published. "
                "The collector does not invent missing end times."
            ),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(output_path)

    return payload


def main():
    parser = argparse.ArgumentParser(
        description="Collect the current week's PGA TOUR TV/streaming schedule."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=45,
        help="Minimum collection confidence score required to publish",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        payload = collect_current_week(args.output, min_score=args.min_score)
    except Exception as exc:
        LOG.error("%s", exc)
        sys.exit(1)

    print(
        f"Wrote {len(payload['coverage'])} coverage windows for "
        f"{payload['tournament']['name']} to {args.output}"
    )
    if payload["collection"]["warnings"]:
        print("Warnings:")
        for warning in payload["collection"]["warnings"]:
            print(f" - {warning}")


if __name__ == "__main__":
    main()
