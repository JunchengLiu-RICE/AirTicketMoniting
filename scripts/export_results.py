"""Export the current price snapshot and seven-day trends."""
import argparse
import json
import sqlite3
import sys
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.platforms import DEFAULT_PLATFORMS, validate_platforms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/prices.db")
    ap.add_argument("--out", default="docs/data/latest.json")
    ap.add_argument("--window-minutes", type=int, default=30,
                    help="把最近 N 分钟的抓取视为\"同一轮\"")
    ap.add_argument("--config", help="仅导出当前配置范围内的航线")
    ap.add_argument("--since-file", help="读取本轮起始时间，适用于超过30分钟的全国扫描")
    args = ap.parse_args()
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) if args.config else None
    selected_platforms = validate_platforms(config.get("platforms", DEFAULT_PLATFORMS) if config else DEFAULT_PLATFORMS)
    platform_clause = "platform IN (" + ",".join("?" for _ in selected_platforms) + ")"
    allowed = {(r["from"], r["to"], d) for r in config.get("routes", []) for d in r["dates"]} if config else None

    db = Path(args.db)
    if not db.exists():
        print("DB not found, skipping export")
        return

    con = sqlite3.connect(str(db))
    source = "flight_prices"
    if allowed is not None:
        # A temporary indexed table avoids long OR expressions and parameter limits.
        con.execute("CREATE TEMP TABLE selected_routes (from_city TEXT, to_city TEXT, depart_date TEXT, "
                    "PRIMARY KEY (from_city, to_city, depart_date)) WITHOUT ROWID")
        con.executemany("INSERT INTO selected_routes VALUES (?,?,?)", allowed)
        source += " JOIN selected_routes USING (from_city, to_city, depart_date)"
    # 找最新的 fetched_at
    latest_ts = con.execute("SELECT MAX(fetched_at) FROM " + source + " WHERE " + platform_clause,
                            selected_platforms).fetchone()[0]
    if not latest_ts:
        if config is None:
            print("No data in DB")
            con.close()
            return
        latest_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cutoff = (datetime.fromisoformat(latest_ts) - timedelta(minutes=args.window_minutes)).isoformat(sep=" ")
    if args.since_file:
        cutoff = Path(args.since_file).read_text(encoding="utf-8").strip()
        datetime.fromisoformat(cutoff)  # Fail explicitly on a malformed run timestamp.
    rows = con.execute(
        "SELECT platform, from_city, to_city, depart_date, price, airline, "
        "flight_no, depart_time, arrive_time, fetched_at, extra "
        "FROM " + source + " WHERE " + platform_clause + " AND fetched_at >= ? ORDER BY fetched_at, id",
        (*selected_platforms, cutoff),
    ).fetchall()

    # 历史走势（近7天）
    hist_rows = con.execute(
        "SELECT depart_date, platform, price, fetched_at, extra "
        "FROM " + source + " WHERE " + platform_clause + " AND fetched_at >= datetime('now','-7 days') ORDER BY fetched_at, id",
        selected_platforms,
    )

    results = [
        {
            "platform": r[0], "from": r[1], "to": r[2], "date": r[3],
            "price": r[4], "airline": r[5], "flight_no": r[6],
            "depart_time": r[7], "arrive_time": r[8], "fetched_at": r[9],
            "extra": json.loads(r[10]) if r[10] else {},
        }
        for r in rows
    ]

    # 汇总 summary
    routes: dict = {}
    for r in results:
        key = (r["from"], r["to"], r["date"])
        routes.setdefault(key, []).append(r)
    summary = []
    for (fc, tc, d), items in sorted(routes.items()):
        best = min(items, key=lambda x: x["price"])
        per_platform = {}
        for item in items:
            platform = item["platform"]
            per_platform[platform] = min(per_platform.get(platform, item["price"]), item["price"])
        summary.append({
            "from": fc, "to": tc, "date": d,
            "per_platform": per_platform,
            "min_price": best["price"],
            "best_platform": best["platform"],
            "best_flight": f"{best['airline']}{best['flight_no']}",
            "best_depart_time": best["depart_time"],
            "best_arrive_time": best["arrive_time"],
            "best_extra": best["extra"],
        })

    if not summary and config is None:
        print("No results in latest window")
        con.close()
        return

    # 从 summary 推断 query 信息
    configured_routes = config.get("routes", []) if config else []
    fallback = configured_routes[0] if configured_routes else {"from": "", "to": ""}
    from_codes = sorted({s["from"] for s in summary}) or [fallback["from"]]
    to_codes = sorted({s["to"] for s in summary}) or [fallback["to"]]
    platforms  = sorted({r["platform"] for r in results})
    depart_dates = sorted({s["date"] for s in summary if s["from"] == from_codes[0]})
    return_dates = sorted({s["date"] for s in summary if s["from"] == to_codes[0]}) if len(to_codes) > 1 else []

    # 历史 trend_data
    trend: dict = {}
    scan_prices = {}
    for d, p, price, ts, extra_text in hist_rows:
        extra = json.loads(extra_text) if extra_text else {}
        key = (d, p, extra.get("scan_id", ts))
        current = scan_prices.get(key)
        if current is None or price < current["v"]:
            scan_prices[key] = {"t": ts, "v": float(price)}
    con.close()
    for (d, p, _), point in sorted(scan_prices.items(), key=lambda item: item[1]["t"]):
        trend.setdefault(d, {}).setdefault(p, []).append(point)

    payload = {
        "ok": True,
        "data": {
            "query": {
                "from": {"code": from_codes[0], "name": from_codes[0]},
                "to":   {"code": to_codes[0],   "name": to_codes[0]},
                "depart_dates": depart_dates,
                "return_dates": return_dates,
                "platforms": platforms,
                "threshold": 0,
                "query_time": latest_ts,
            },
            "results": results,
            "summary": summary,
            "trend": trend,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }
    if config and config.get("search_scope"):
        payload["data"]["query"]["from"] = {"code": "中国", "name": "全国（含 HKG）"}
        targets = config["search_scope"]["destinations"]
        payload["data"]["query"]["to"] = {"code": "/".join(targets), "name": " / ".join(targets)}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(results)} records → {out}")


if __name__ == "__main__":
    main()
