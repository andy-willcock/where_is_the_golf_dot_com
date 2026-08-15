from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from collector import collect_current_week, CollectionError


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "schedule.json"

app = Flask(__name__, static_folder=".", static_url_path="")


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


@app.get("/api/schedule")
def api_schedule():
    return jsonify(load_schedule())


@app.post("/api/refresh")
def api_refresh():
    """
    Local-development refresh endpoint.

    Do not expose this endpoint publicly without authentication/rate limiting.
    """
    try:
        payload = collect_current_week(DATA_FILE)
        return jsonify({"ok": True, "schedule": payload})
    except CollectionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


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
