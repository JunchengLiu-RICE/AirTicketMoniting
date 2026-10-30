# AirTicketMoniting

[![Tests](https://github.com/JunchengLiu-RICE/AirTicketMoniting/actions/workflows/test.yml/badge.svg)](https://github.com/JunchengLiu-RICE/AirTicketMoniting/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A reusable flight-price monitor. Set your airports, dates and cabin in **one file**. Google Flights finds the lowest qualifying fare; optional ServerChan notifications reach WeChat when the first fare is found and whenever a later fare is lower.

## Configure a trip

Edit [`trip.yaml`](trip.yaml):

```yaml
origin: JFK
destination: LHR
dates: ["2030-06-15", "2030-06-16"]
cabin: economy
```

These are illustrative airports and dates. Replace them with your own. The repository ships with `dates: []`, so publishing, cloning or running the unconfigured template does not query flights or send notifications.

| Setting | Accepted values |
|---|---|
| `origin`, `destination` | Airport IATA codes, such as `JFK`, `LHR`, `PEK`, `HKG`. Up to seven alternatives per side can be supplied as a YAML list. |
| `dates` | One or more departure dates in `YYYY-MM-DD` format; an empty list disables searches. |
| `cabin` | `economy`, `premium_economy`, `business`, or `first`. |

The default search is one-way for one adult, priced in CNY. Google Flights results are checked against the selected cabin on **every flown segment**. Connections and next-day arrivals are allowed. If no fare meets the requirements, the monitor records no quote and sends no price alert. Departure dates and optional departure times refer to the departure airport's local time; expired dates are filtered using the runner's UTC calendar date.

## Run on GitHub Actions

1. Select **Use this template → Create a new repository**. Choose **Private** for personal travel monitoring.
2. Edit the four settings in `trip.yaml` and commit the change.
3. Open **Actions → AirTicketMoniting → Run workflow** for an immediate check.
4. Open the run's Summary or download the `flight-report` artifact for its HTML report, JSON results and logs.

Checks run at **01:00, 07:00, 13:00 and 19:00 UTC**. GitHub can delay scheduled runs. A `trip.yaml` change also triggers a check. History and the last successfully notified price are saved on the separate `monitor-state` branch. Editing your trip file takes effect on the next run; there are no hidden saved itinerary overrides.

This template works in public or private repositories. **In a public repository, trip settings, Actions logs, report artifacts and the `monitor-state` branch are public.** The public template contains no personal itinerary, saved prices or credentials. Create a private copy before entering travel details you want to keep private. The project does not publish GitHub Pages.

### Optional WeChat notifications

Sign in to [ServerChan](https://sct.ftqq.com/), obtain a SendKey, and add it under **Settings → Secrets and variables → Actions → New repository secret**, named **`SERVERCHAN_KEY`**. Do not put the key in `trip.yaml` or source code. This repository has no preconfigured SendKey.

Without a key, queries and reports still work. Alerts are sent for the first qualifying fare and then only when the price is lower than the last successfully sent notification. An unchanged fare does not notify, including manual runs. Changing the route, cabin or optional filters starts a separate notification baseline.

## Run locally

Requires Python 3.12 or 3.13 and network access to Google Flights.

```bash
git clone https://github.com/JunchengLiu-RICE/AirTicketMoniting.git
cd AirTicketMoniting
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# Edit trip.yaml, then run one check:
python main.py --once
# Or leave the local scheduler running (every 90 minutes):
python main.py
```

To generate the same report locally:

```bash
python scripts/prepare_actions_config.py
python main.py --once -c .runtime/config.yaml
python scripts/export_results.py --db data/prices.db --config .runtime/config.yaml --since-file .runtime/started_at --out docs/data/latest.json
python scripts/render_report.py --results-file docs/data/latest.json --out-dir docs/data
```

Open `docs/data/report.html`. If all dates are absent or expired, the Actions workflow skips searching; no price report is generated. For local WeChat notifications, set `SERVERCHAN_KEY` in the process environment before running the monitor.

## Optional settings

Add only the settings you need to `trip.yaml`:

```yaml
return_dates: ["2030-06-22"] # Omit for a one-way trip
airlines: [BA]              # Marketing-carrier IATA codes; omit for any airline
max_stops: 1               # Maximum connections; omit for no limit
depart_after: "18:00"      # Strictly later than this departure-local time
```

The validated Google Flights search is the default. Fliggy and Ctrip crawlers are also included for optional economy price comparison:

```yaml
platforms: [google_flights, fliggy, ctrip]
```

Fliggy and Ctrip do not verify individual cabin or airline/time/connection restrictions, so those advanced filters and non-economy cabins require Google Flights alone. Multi-platform economy results are indicative comparison fares. Ctrip requires `python -m playwright install chromium`; Google Flights and Fliggy do not require a browser. Platform verification pages or anti-bot restrictions can prevent results.

## Development and attribution

```bash
python -m unittest discover -s tests -v
```

Batch SQLite writes, scoped report queries and deferred browser imports are retained from the optimized source. Tests use synthetic itineraries and mocked flight responses; they do not need credentials or send WeChat messages. Fares must be confirmed with the airline or booking provider before purchase.

Derived from [Flight Price Monitor by Long Zhang](https://github.com/zhanglong-ustc/flight-price-monitor). See [NOTICE](NOTICE) and the original [MIT License](LICENSE).
