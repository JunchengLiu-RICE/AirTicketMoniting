"""手动刷新全国定期民航机场清单（OurAirports 公共领域数据）。"""
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main():
    source = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
    response = httpx.get(source, timeout=60, follow_redirects=True)
    response.raise_for_status()
    airports = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        if row["iso_country"] not in {"CN", "HK", "MO", "TW"} or row["scheduled_service"] != "yes":
            continue
        code = row["iata_code"]
        if len(code) == 3 and row["type"] in {"large_airport", "medium_airport", "small_airport"}:
            airports[code] = {"code": code, "name": row["name"], "region": row["iso_country"]}
    if "HKG" not in airports or not {"CTU", "TFU", "PEK", "PVG"} <= airports.keys():
        raise ValueError("Airport source is incomplete; existing catalog was not replaced")
    payload = {"source": source, "license": "Public Domain", "updated_at": datetime.now(timezone.utc).isoformat(),
               "airports": [airports[k] for k in sorted(airports)]}
    output = Path(__file__).resolve().parents[1] / "core/china_airports.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Scheduled airports:", len(airports))
    print({r: sum(a["region"] == r for a in airports.values()) for r in ("CN", "HK", "MO", "TW")})


if __name__ == "__main__":
    main()
