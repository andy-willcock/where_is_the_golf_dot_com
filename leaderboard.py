from __future__ import annotations
import json, re
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

CBS_LEADERBOARD_URL = "https://www.cbssports.com/golf/leaderboard/pga-tour/"
HEADERS={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36","Accept-Language":"en-US,en;q=0.9"}
BASE_DIR=Path(__file__).resolve().parent
SNAPSHOT_FILE=BASE_DIR/"data"/"leaderboard.json"

def clean(text): return re.sub(r"\s+"," ",text or "").strip()

def fetch_cbs_leaderboard(timeout=20):
    r=requests.get(CBS_LEADERBOARD_URL,headers=HEADERS,timeout=timeout)
    r.raise_for_status()
    return parse_cbs_leaderboard(r.text)

def parse_cbs_leaderboard(html):
    soup=BeautifulSoup(html,"html.parser")
    title=clean(soup.title.get_text(" ",strip=True) if soup.title else "")
    m=re.search(r"^\d{4}\s+(.+?)\s+Leaderboard\b",title,re.I)
    tournament=clean(m.group(1)) if m else ""
    table=None
    for candidate in soup.find_all("table"):
        header=clean(" ".join(x.get_text(" ",strip=True) for x in candidate.find_all("th"))).lower()
        if "pos" in header and "name" in header and ("to par" in header or "thru" in header):
            table=candidate; break
    if table is None:
        for candidate in soup.find_all("table"):
            txt=clean(candidate.get_text(" ",strip=True)).lower()
            if "pos" in txt and "to par" in txt and "thru" in txt:
                table=candidate; break
    if table is None: raise RuntimeError("CBS leaderboard table was not found.")
    rows=[]
    for tr in table.find_all("tr"):
        cells=[clean(x.get_text(" ",strip=True)) for x in tr.find_all(["th","td"])]
        if not cells or len(cells)<6: continue
        low=[c.lower() for c in cells]
        if "pos" in low or ("name" in low and "to par" in low): continue
        name=cells[2] if len(cells)>=3 else ""
        if not name or name.lower()=="name": continue
        rows.append({"position":cells[0],"player":name,"toPar":cells[3] if len(cells)>3 else "","thru":cells[4] if len(cells)>4 else "","today":cells[5] if len(cells)>5 else "","r1":cells[6] if len(cells)>6 else "","r2":cells[7] if len(cells)>7 else "","r3":cells[8] if len(cells)>8 else "","r4":cells[9] if len(cells)>9 else "","total":cells[10] if len(cells)>10 else ""})
    if not rows: raise RuntimeError("CBS leaderboard parsed zero player rows.")
    ranked=[r for r in rows if r["position"] not in ("","-")]
    unranked=[r for r in rows if r["position"] in ("","-")]
    return {"tournament":tournament,"source":CBS_LEADERBOARD_URL,"updatedUtc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"players":(ranked+unranked)[:15]}

def save_snapshot(payload,path=SNAPSHOT_FILE):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    tmp.replace(path)

def load_snapshot(path=SNAPSHOT_FILE):
    if not path.exists(): return {"tournament":"","source":CBS_LEADERBOARD_URL,"updatedUtc":None,"players":[]}
    return json.loads(path.read_text(encoding="utf-8"))

def refresh_snapshot():
    payload=fetch_cbs_leaderboard()
    save_snapshot(payload)
    return payload
