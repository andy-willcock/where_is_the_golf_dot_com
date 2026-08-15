from __future__ import annotations

import json
from pathlib import Path

path = Path("data/schedule.json")

if not path.exists():
    raise SystemExit("data/schedule.json does not exist")

data = json.loads(path.read_text(encoding="utf-8"))

print("Tournament:", data.get("tournament", {}).get("name"))
print("Updated:", data.get("lastUpdatedUtc"))
print("Coverage windows:", len(data.get("coverage", [])))
print("Score:", data.get("collection", {}).get("score"))
print("Warnings:")
for warning in data.get("collection", {}).get("warnings", []):
    print(" -", warning)
print("Sources:")
for source in data.get("collection", {}).get("sources", []):
    print(" -", source.get("domain"), source.get("parsedWindows"), source.get("url"))
