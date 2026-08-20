from __future__ import annotations

import json
import os
import logging
import traceback
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from collector import collect_current_week, CollectionError
from leaderboard import fetch_cbs_leaderboard, load_snapshot


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "schedule.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)

app = Flask(__name__, static_folder=".", static_url_path="")
LEADERBOARD_CACHE = {"payload": None, "fetched_at": 0.0}
LEADERBOARD_CACHE_SECONDS = 60


def load_schedule():
    if not DATA_FILE.exists():
        return {
            "lastUpdatedUtc": None,
            "tournament": {"name": "Schedule not collected yet"},
            "coverage": [],
            "collection": {"warnings": ["Run python collector.py first."]},
        }

    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/leaderboard")
def api_leaderboard():
    now = time.time()
    if LEADERBOARD_CACHE["payload"] is not None and now - LEADERBOARD_CACHE["fetched_at"] < LEADERBOARD_CACHE_SECONDS:
        return jsonify(LEADERBOARD_CACHE["payload"])
    try:
        payload = fetch_cbs_leaderboard()
        payload["live"] = True
        LEADERBOARD_CACHE["payload"] = payload
        LEADERBOARD_CACHE["fetched_at"] = now
        return jsonify(payload)
    except Exception as exc:
        app.logger.warning("Live leaderboard fetch failed: %s", exc)
        payload = load_snapshot()
        payload["live"] = False
        payload["warning"] = "Live CBS fetch unavailable; showing GitHub-collected snapshot."
        return jsonify(payload)


@app.get("/api/schedule")
def api_schedule():
    return jsonify(load_schedule())


@app.post("/api/refresh")
def api_refresh():
    """
    Refresh schedule collection.

    The exception is explicitly logged because Gunicorn does not automatically
    print exceptions that Flask converts into JSON responses.
    """
    app.logger.warning("Manual schedule refresh requested")

    try:
        payload = collect_current_week(DATA_FILE)
        app.logger.warning(
            "Schedule refresh succeeded: %s (%d coverage windows)",
            payload.get("tournament", {}).get("name"),
            len(payload.get("coverage", [])),
        )
        return jsonify({"ok": True, "schedule": payload})
    except Exception as exc:
        app.logger.error("Schedule refresh failed: %s", exc)
        app.logger.error(traceback.format_exc())
        return jsonify({
            "ok": False,
            "error": str(exc),
            "errorType": type(exc).__name__,
        }), 502


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(BASE_DIR, path)


def refresh_loop(hours: int):
    while True:
        try:
            collect_current_week(DATA_FILE)
            print("[collector] schedule refreshed")
        except Exception as exc:
            print(f"[collector] refresh failed; keeping previous data: {exc}")

        time.sleep(hours * 60 * 60)


if __name__ == "__main__":
    # AUTO_REFRESH_HOURS=6 refreshes immediately on startup and every six hours.
    refresh_hours = int(os.getenv("AUTO_REFRESH_HOURS", "0"))

    if refresh_hours > 0:
        thread = threading.Thread(
            target=refresh_loop,
            args=(refresh_hours,),
            daemon=True,
        )
        thread.start()

    app.run(
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
        port=int(os.getenv("PORT", "5000")),
        use_reloader=False,
    )
