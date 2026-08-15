# PGA TOUR TV Dashboard — Automatic Python Collector

This version adds a Python collection pipeline to the timezone-aware dashboard.

## What it does

1. Identifies the current PGA TOUR tournament from a public schedule page.
2. Searches the web for reputable weekly viewing guides.
3. Fetches several high-priority sources.
4. Parses complete start/end broadcast windows.
5. Converts Eastern Time source schedules to UTC.
6. Deduplicates overlapping source information.
7. Validates the result and refuses to publish low-confidence data.
8. Writes `data/schedule.json`.
9. The browser converts UTC into each visitor's local timezone.

## Install

```bash
cd pga_tv_dashboard_auto

python -m venv .venv
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt
```

### Optional browser fallback

Some publishers block ordinary HTTP requests. The collector automatically uses
Playwright if it is installed:

```bash
pip install -r requirements-browser.txt
playwright install chromium
```

This is strongly recommended for a deployed collector.

## Collect the current week

```bash
python collector.py
```

Verbose mode:

```bash
python collector.py --verbose
```

The output is:

```text
data/schedule.json
```

## Run the website

```bash
python server.py
```

Open:

```text
http://127.0.0.1:5000
```

The **Refresh schedule** button executes the collector and keeps the previous
schedule if collection fails.

## Automatic refresh

For a simple local deployment, refresh every 6 hours:

macOS/Linux:

```bash
AUTO_REFRESH_HOURS=6 python server.py
```

Windows PowerShell:

```powershell
$env:AUTO_REFRESH_HOURS="6"
python server.py
```

For a production deployment, a cron job or scheduled cloud task is preferable
to the in-process loop.

Example cron entry for 6:10 a.m. Eastern every Thursday-Sunday:

```cron
10 6 * * 4-7 cd /path/to/pga_tv_dashboard_auto && /path/to/.venv/bin/python collector.py
```

## Production recommendation

A scheduled job should run the collector before the first round and several
times per day during the event. Publish the JSON only when validation passes.
The collector writes through a temporary file before replacing the live
schedule, so a failed scrape will not wipe out the previous working data.

## Source strategy

The project ranks sources approximately as:

1. PGA TOUR
2. Golf Channel
3. NBC Sports
4. CBS Sports
5. ESPN

ESPN/PGA TOUR schedule pages are used to identify the event. Detailed viewing
guides are used for actual start/end windows.

Search discovery uses `ddgs`, so no paid search API key is required.

## Important limitations

Web pages change. Any scraper will eventually need selector/parser maintenance.

The parser intentionally **does not invent missing end times**. A source saying
"coverage begins at 9 a.m." is not enough to create a dashboard time window.

The collector currently assumes U.S. viewing-guide times are Eastern Time,
which is how the target golf sources generally publish schedules. If you add a
source that publishes another timezone, add source-specific timezone handling.

Review each publisher's terms of service before operating a scraper at scale.
For a commercial site, a licensed schedule/data feed is preferable.

## Tests

```bash
pip install pytest
pytest -q
```

The included fixture covers:
- inherited meridiem (`1-3 p.m.`)
- TV providers
- streaming providers
- simulcasts such as CBS + Paramount+
- Thursday-Sunday round mapping


# Deploying whereisthegolf.com on Render

This folder is deployment-ready for Render.

## 1. Push this folder to GitHub

Create a new GitHub repository, for example:

```text
whereisthegolf
```

Upload or push all files in this folder to that repository.

## 2. Deploy on Render

In Render:

1. Choose **New > Blueprint** if you want Render to read `render.yaml`.
2. Connect your GitHub account.
3. Choose the `whereisthegolf` repository.
4. Deploy the Blueprint.

Alternatively choose **New > Web Service** and enter:

```text
Build command:
pip install -r requirements.txt
```

```text
Start command:
gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

After deployment, Render will provide a temporary address similar to:

```text
https://whereisthegolf.onrender.com
```

Test that address before connecting your custom domain.

## 3. Add your domain in Render

In your Render Web Service:

```text
Settings > Custom Domains > Add Custom Domain
```

Add:

```text
whereisthegolf.com
```

Also add:

```text
www.whereisthegolf.com
```

Render will display the exact DNS records it expects. Use those values in
GoDaddy rather than guessing them.

## 4. Configure GoDaddy DNS

Open:

```text
GoDaddy > My Products > Domains > whereisthegolf.com > DNS
```

Create or edit the records Render asks for.

Typically the setup consists of:

- the root/apex domain `@` pointing to Render as instructed by Render;
- `www` as a CNAME pointing to the Render hostname.

Do not delete unrelated MX/TXT records if you use email or other domain
services.

## 5. Verify the domain

Return to Render's Custom Domains section and click **Verify** if required.

After DNS resolves, Render provisions a managed TLS certificate so the site
loads at:

```text
https://whereisthegolf.com
```

## Production note about collection

The current collector writes the most recently validated schedule to a local
JSON file. Render instances can restart and local files are not durable storage.

This project compensates by refreshing the schedule when the process starts
when `AUTO_REFRESH_HOURS` is enabled, then refreshing periodically while the
instance is running.

For a higher-reliability production deployment, move schedule storage to a
database such as Postgres, Redis, or an object store.


## v2 tournament-discovery fix

This build fixes a production issue where ESPN collapses a schedule row into
one block of text. The previous parser expected the date to be on a standalone
line and could therefore return zero tournaments.

v2:
- parses schedule rows independent of HTML line breaks;
- keeps PGA TOUR as an independent fallback;
- adds a DDGS/search-snippet fallback across ESPN, PGA TOUR and CBS Sports;
- logs the number of tournaments parsed from each source;
- includes regression tests for collapsed ESPN schedule HTML.

After pushing this version to GitHub, Render should automatically redeploy.
Open the Render logs and click `Refresh schedule`. Useful log messages include:

```text
Parsed 40 tournaments from https://www.espn.com/golf/schedule
Discovered active tournament from ...
Current tournament: FedEx St. Jude Championship (2026-08-13 to 2026-08-16)
```


## v3 cloud-hosting reliability fix

v3 changes tournament discovery to use ESPN's JSON scoreboard endpoint first:

```text
https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard
```

The endpoint currently exposes the active event, full PGA TOUR season calendar,
and broadcast-provider names. HTML scraping remains only as fallback.

v3 also:
- prints refresh errors and full tracebacks into Render logs;
- logs tournament discovery at WARNING level so Gunicorn surfaces it;
- prioritizes CBS Sports viewing-guide searches;
- adds an ESPN JSON regression test.

After deployment, a refresh should produce log lines beginning with:

```text
Manual schedule refresh requested
Tournament discovery: ESPN JSON API -> ...
Current tournament: ...
Searching for viewing guides for: ...
```

If the next failure is in viewing-guide search or parsing, those lines will now
show exactly where it occurs.


# v4 — GitHub Actions collection architecture

Render is now used only to serve the Flask website.

The live Render process **does not scrape ESPN/PGA TOUR** because sports sites
may block Render datacenter IP addresses with HTTP 403 responses.

Instead:

```text
GitHub Actions
     |
     | runs collector.py
     v
data/schedule.json
     |
     | git commit + push
     v
GitHub main branch
     |
     | Render auto-deploy
     v
whereisthegolf.com
```

## First-time GitHub setup

After pushing v4, go to your GitHub repository:

```text
Actions > Refresh PGA Schedule
```

Click:

```text
Run workflow
```

This performs an immediate collection test.

If GitHub says the workflow cannot push changes, go to:

```text
Repository Settings
> Actions
> General
> Workflow permissions
```

and allow workflows to have read/write repository permission.

The workflow itself also declares:

```yaml
permissions:
  contents: write
```

## Automatic schedule

`.github/workflows/refresh-schedule.yml` runs:

- Monday, to discover the next week's tournament.
- Thursday-Sunday several times per day.
- Any time you manually click **Run workflow**.

GitHub Actions cron expressions use UTC.

## What happens after a successful run

If `data/schedule.json` changes, the workflow commits:

```text
Update PGA TOUR schedule
```

to `main`.

If Render Auto-Deploy is enabled for the linked branch, Render then deploys
that commit automatically.

## Render setting

`AUTO_REFRESH_HOURS` is now `0`.

Do not rely on `/api/refresh` for production collection. The browser refresh
button has been removed because Render's outgoing requests were receiving
403 responses from ESPN.

## Debugging

Open:

```text
GitHub > Actions > Refresh PGA Schedule > latest run
```

The collection output is visible in the **Collect current PGA TOUR schedule**
step.

The workflow then validates:

- tournament name exists;
- at least one complete coverage window exists;
- collector score is at least 45.

If validation fails, it does not commit bad schedule data.


# v5 — ESPN removed from tournament discovery

GitHub Actions was also receiving HTTP 403 from ESPN's scoreboard endpoint.

v5 removes ESPN from the critical discovery path completely.

Tournament discovery is now:

```text
CBS Sports PGA Tour schedule
        |
        v
PGA TOUR official schedule
        |
        v
search-result fallback
```

CBS Sports publishes a season schedule containing the date range, tournament,
location, course and broadcast-network column. The official PGA TOUR schedule
is retained as the second source.

The collector no longer calls ESPN's scoreboard JSON endpoint when deciding
which tournament is current.


# v7 — PGA TOUR Media broadcast schedule as primary source

Primary data source:

```text
https://pgatourmedia.pgatourhq.com/broadcast-schedule
```

This page already publishes structured current-week fields for PGA TOUR:

- Tournament
- Round
- Date
- Airtime
- Network
- Content Type

The collector parses that source directly, converts Eastern airtimes to UTC,
and excludes audio-only entries such as SiriusXM.

The older CBS / PGA TOUR / viewing-guide discovery logic remains as fallback
only if the media schedule is unavailable.


# v8 — direct PGA TOUR Media record parsing

v8 replaces the line-by-line media parser with a direct six-field parser.

It now captures each block from:

```text
Tournament
Round
Date
Airtime
Network
Content Type
```

and only then filters the records to the selected PGA TOUR tournament.

This prevents Saturday/Sunday records from being lost during a second parsing
pass. Collector logs now include a record count by round, for example:

```text
PGA TOUR Media records by round:
{'Round 1': 4, 'Round 2': 4, 'Round 3': 5, 'Final Round': 4}
```

Audio records are excluded from the web schedule.
