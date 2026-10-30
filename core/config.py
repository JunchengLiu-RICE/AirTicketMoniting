"""Convert the public, reusable trip settings into the internal monitor format."""
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
import re

from .google_flights import airport_codes
from .platforms import validate_platforms


CABINS = {"economy", "premium_economy", "business", "first"}
FIELDS = {"origin", "destination", "dates", "cabin", "return_dates", "airlines",
          "max_stops", "depart_after", "platforms"}


def _dates(value):
    values = [value] if isinstance(value, (str, date)) else value
    if not isinstance(values, list):
        raise ValueError("dates and return_dates must be lists of YYYY-MM-DD dates")
    output = []
    for item in values:
        if isinstance(item, date) and not isinstance(item, datetime):
            item = item.isoformat()
        if not isinstance(item, str) or date.fromisoformat(item).isoformat() != item:
            raise ValueError("Use dates in YYYY-MM-DD format")
        output.append(item)
    return list(dict.fromkeys(output))


def build_config(settings, today=None):
    if not isinstance(settings, dict) or set(settings) - FIELDS:
        raise ValueError("Unknown trip settings; supported fields: " + ", ".join(sorted(FIELDS)))
    try:
        origins = airport_codes(settings.get("origin", ""))
        destinations = airport_codes(settings.get("destination", ""))
    except (TypeError, AttributeError) as exc:
        raise ValueError("origin and destination must be airport IATA codes") from exc
    if set(origins) & set(destinations):
        raise ValueError("Origin and destination airports must differ")
    cabin = settings.get("cabin", "economy")
    if not isinstance(cabin, str) or cabin not in CABINS:
        raise ValueError("cabin must be one of: " + ", ".join(sorted(CABINS)))
    platforms = validate_platforms(settings.get("platforms", ["google_flights"]))
    if cabin != "economy" and set(platforms) != {"google_flights"}:
        raise ValueError("Cabin-specific searches require platforms: [google_flights]")
    airlines = settings.get("airlines", [])
    if not isinstance(airlines, list) or any(not isinstance(code, str) for code in airlines):
        raise ValueError("airlines must be a list of two-character IATA codes")
    airlines = list(dict.fromkeys(code.upper() for code in airlines))
    if any(not re.fullmatch(r"[A-Z0-9]{2}", code) for code in airlines):
        raise ValueError("airlines must contain two-character IATA codes")
    stops = settings.get("max_stops")
    if stops is not None and (isinstance(stops, bool) or not isinstance(stops, int) or stops < 0):
        raise ValueError("max_stops must be a non-negative integer or null")
    after = settings.get("depart_after")
    if after is not None and (not isinstance(after, str) or time.fromisoformat(after).strftime("%H:%M") != after):
        raise ValueError("depart_after must be a departure-local HH:MM string")
    if (airlines or stops is not None or after) and set(platforms) != {"google_flights"}:
        raise ValueError("Airline, stop and time filters require platforms: [google_flights]")
    options = {"cabin": cabin, "verify_cabin": True, "airlines": airlines,
               "max_stops": stops, "depart_after": after}
    today = today or datetime.now(timezone.utc).date()
    routes = []
    for sources, targets, dates in (
        (origins, destinations, _dates(settings.get("dates", []))),
        (destinations, origins, _dates(settings.get("return_dates", []))),
    ):
        dates = [value for value in dates if date.fromisoformat(value) >= today]
        if not dates:
            continue
        group = hashlib.sha256(json.dumps([sources, targets, options, platforms], sort_keys=True).encode()).hexdigest()[:16]
        routes.extend({"from": origin, "to": destination, "dates": dates,
                       "alert_on_drop": True, "alert_group": "trip-" + group}
                      for origin in sources for destination in targets)
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    return {
        "routes": routes, "platforms": platforms,
        "crawler": {"headless": True, "timeout_seconds": 45, "delay_min": 2, "delay_max": 5,
                    "google_flights": options},
        "schedule": {"interval_minutes": 90, "jitter_minutes": 0, "run_on_start": True},
        "output": {"db_path": "data/prices.db", "log_path": "logs/monitor.log"},
        "notifier": {"serverchan": {"enabled": bool(key), "send_key": key}},
    }
