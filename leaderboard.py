from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_FILE = BASE_DIR / "data" / "leaderboard.json"
SCHEDULE_FILE = BASE_DIR / "data" / "schedule.json"
PGA_SOURCE_URL = "https://www.pgatour.com/leaderboard"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def load_current_tournament_name() -> str:
    if not SCHEDULE_FILE.exists():
        return ""

    data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    return data.get("tournament", {}).get("name", "") or ""


def choose_tournament_id(schedule_df, tournament_name: str) -> tuple[str, str]:
    """
    Find the current event in PGA TOUR's season schedule.

    Primary match is the tournament already selected by our broadcast collector.
    If that name is unavailable, fall back to an event whose status indicates
    it is currently in progress.
    """
    if schedule_df is None or getattr(schedule_df, "empty", True):
        raise RuntimeError("PGA TOUR schedule API returned no tournaments")

    target = normalize_name(tournament_name)

    if target:
        best = None
        best_score = -1

        for _, row in schedule_df.iterrows():
            candidate_name = str(row.get("tournament_name") or "")
            candidate = normalize_name(candidate_name)

            if not candidate:
                continue

            if candidate == target:
                return str(row["tournament_id"]), candidate_name

            # Lightweight token overlap handles small naming differences such
            # as sponsor prefixes/suffixes.
            target_tokens = set(target.split())
            candidate_tokens = set(candidate.split())
            overlap = len(target_tokens & candidate_tokens)
            union = len(target_tokens | candidate_tokens) or 1
            score = overlap / union

            if score > best_score:
                best_score = score
                best = row

        if best is not None and best_score >= 0.55:
            return str(best["tournament_id"]), str(best.get("tournament_name") or tournament_name)

    # Fallback to status if the local schedule name could not be matched.
    for _, row in schedule_df.iterrows():
        status = str(row.get("status") or "").upper()
        if any(token in status for token in ("PROGRESS", "ACTIVE", "LIVE", "STARTED")):
            return str(row["tournament_id"]), str(row.get("tournament_name") or "")

    raise RuntimeError(
        f"Could not match current tournament {tournament_name!r} "
        "to PGA TOUR season schedule"
    )


def _value(row, *keys, default=""):
    for key in keys:
        try:
            value = row.get(key)
        except Exception:
            value = None
        if value is not None and str(value) not in ("nan", "None"):
            return value
    return default


def build_payload_from_current_leaders(df, tournament_name: str, tournament_id: str) -> dict:
    if df is None or getattr(df, "empty", True):
        raise RuntimeError("PGA TOUR current-leaders API returned no players")

    players = []

    for _, row in df.iterrows():
        display_name = str(_value(row, "display_name", default="")).strip()

        if not display_name:
            first = str(_value(row, "first_name", default="")).strip()
            last = str(_value(row, "last_name", default="")).strip()
            display_name = f"{first} {last}".strip()

        if not display_name:
            continue

        position = str(_value(row, "position", default="—"))
        total_score = str(_value(row, "total_score", "total", default="—"))
        thru = str(_value(row, "thru", default="—"))
        round_score = str(_value(row, "round_score", "score", default="—"))

        players.append({
            "position": position,
            "player": display_name,
            "toPar": total_score,
            "thru": thru,
            "today": round_score,
        })

        if len(players) == 15:
            break

    if len(players) < 15:
        raise RuntimeError(
            f"PGA TOUR current-leaders API returned only {len(players)} usable players"
        )

    return {
        "tournament": tournament_name,
        "tournamentId": tournament_id,
        "source": PGA_SOURCE_URL,
        "updatedUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "players": players,
    }


def fetch_pga_leaderboard() -> dict:
    """
    Get the current top 15 using PGA TOUR's own API.

    pgatourPY wraps the same PGA TOUR GraphQL/data endpoints used by pgatour.com.
    """
    import pgatourpy as pga

    current_name = load_current_tournament_name()
    year = datetime.now(timezone.utc).year

    schedule = pga.pga_schedule(year)
    tournament_id, official_name = choose_tournament_id(schedule, current_name)

    # PGA TOUR exposes a dedicated top-15 snapshot.
    leaders = pga.pga_current_leaders(tournament_id)

    # Fallback to the full leaderboard if the compressed current-leaders
    # operation is temporarily unavailable.
    if leaders is None or leaders.empty:
        leaders = pga.pga_leaderboard(tournament_id)

        # Standardize full-leaderboard columns to the current-leaders schema.
        if leaders is not None and not leaders.empty:
            rename = {}
            if "total" in leaders.columns:
                rename["total"] = "total_score"
            if "score" in leaders.columns:
                rename["score"] = "round_score"
            leaders = leaders.rename(columns=rename)

    return build_payload_from_current_leaders(
        leaders,
        official_name or current_name,
        tournament_id,
    )


def save_snapshot(payload: dict, path: Path = SNAPSHOT_FILE) -> None:
    if len(payload.get("players", [])) < 15:
        raise RuntimeError("Refusing to save leaderboard with fewer than 15 players")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def load_snapshot(path: Path = SNAPSHOT_FILE) -> dict:
    if not path.exists():
        return {
            "tournament": "",
            "source": PGA_SOURCE_URL,
            "updatedUtc": None,
            "players": [],
        }

    return json.loads(path.read_text(encoding="utf-8"))


def refresh_snapshot() -> dict:
    payload = fetch_pga_leaderboard()
    save_snapshot(payload)
    return payload


if __name__ == "__main__":
    payload = refresh_snapshot()
    print("Tournament:", payload.get("tournament"))
    print("Tournament ID:", payload.get("tournamentId"))
    print("Rows:", len(payload.get("players", [])))
    print("Source:", payload.get("source"))
