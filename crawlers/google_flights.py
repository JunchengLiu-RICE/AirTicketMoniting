"""Google Flights：读取查询页的航班报价，不采集价格日历或推荐日期。"""
import json
import math
from html.parser import HTMLParser
from pathlib import Path
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, time
from functools import lru_cache

import httpx

from core.google_flights import airport_codes, build_google_flights_url
from core.models import FlightPrice
from .base import BaseCrawler


@lru_cache(maxsize=1)
def _airport_regions():
    return json.loads((Path(__file__).resolve().parents[1] / "core/airport_regions.json").read_text(encoding="utf-8"))


class _FlightDataParser(HTMLParser):
    """只读取 Google Flights 的 ds:1 航班结果数据。"""

    def __init__(self):
        super().__init__()
        self.in_results = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.in_results = "ds:1" in dict(attrs).get("class", "").split()

    def handle_data(self, data):
        if self.in_results:
            self.scripts.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_results = False


class GoogleFlightsCrawler(BaseCrawler):
    name = "google_flights"

    def __init__(self, config, logger):
        super().__init__(config, logger)
        self.options = config.get("google_flights", {})
        self.cabin = self.options.get("cabin", "economy")
        self.require_ocean_business = self.options.get("require_ocean_business", False)
        self.hidden_city_endpoints = self.options.get("hidden_city_endpoints", [])
        self.airlines = self.options.get("airlines", [])
        self.max_stops = self.options.get("max_stops")
        self.depart_after = self.options.get("depart_after")
        if self.depart_after:
            if time.fromisoformat(self.depart_after).strftime("%H:%M") != self.depart_after:
                raise ValueError("depart_after 需为出发机场当地时间 HH:MM")
        self.verify_cabin = self.options.get("verify_cabin", False)
        self.query_stats = {"attempted": 0, "parsed": 0, "failed": 0}

    def login_url(self) -> str:
        return "https://www.google.com/travel/flights?hl=en&curr=CNY"

    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        origins, destinations = airport_codes(from_city), airport_codes(to_city)
        from_label, to_label = "/".join(origins), "/".join(destinations)
        results = []
        with httpx.Client(
            headers={"User-Agent": self._ua(), "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=self.timeout_ms / 1000,
        ) as client:
            for date in dates:
                best = None
                endpoints = [destinations, *[e for e in dict.fromkeys(self.hidden_city_endpoints) if e not in destinations]]
                for endpoint in endpoints:
                    hidden_search = isinstance(endpoint, str)
                    if hidden_search and endpoint in origins:
                        continue
                    response = None
                    try:
                        url = build_google_flights_url(origins, endpoint, date, self.cabin,
                                                       destinations if hidden_search else "",
                                                       airlines=self.airlines, max_stops=self.max_stops,
                                                       earliest_departure_hour=int(self.depart_after[:2]) if self.depart_after else None)
                        self.query_stats["attempted"] += 1
                        response = client.get(url)
                        response.raise_for_status()
                        prices = self._extract_flights(response.text, origins[0], destinations[0], date)
                        self.query_stats["parsed"] += 1
                        for price in prices:
                            extra = json.loads(price.extra)
                            segments = extra["segments"]
                            if segments[0]["from"] not in origins:
                                continue
                            stops = [i for i, s in enumerate(segments) if s["to"] in destinations]
                            if not stops:
                                continue
                            end = stops[0] if hidden_search else len(segments) - 1
                            if not hidden_search and segments[end]["to"] not in destinations:
                                continue
                            flown = segments[:end + 1]
                            if self.require_ocean_business and not self._ocean_business(flown):
                                continue
                            if not self._matches_filters(flown, date):
                                continue
                            if best is not None and price.price >= best.price:
                                continue
                            actual_destination = flown[-1]["to"]
                            actual_origin = flown[0]["from"]
                            # Links must reproduce the quoted set of airports and ticket endpoint.
                            extra.update(currency="CNY", url=url, ticket_destination=segments[-1]["to"],
                                         hidden_city=end < len(segments) - 1, actual_destination=actual_destination,
                                         ocean_business_verified=self.require_ocean_business)
                            if self.airlines or self.depart_after or self.max_stops is not None or self.verify_cabin:
                                extra["filters_verified"] = {
                                    "airlines": self.airlines, "cabin": self.cabin,
                                    "cabin_verified": self.verify_cabin,
                                    "max_stops": self.max_stops, "depart_after": self.depart_after,
                                    "time_basis": "departure_airport_local",
                                }
                            price.from_city, price.to_city = actual_origin, actual_destination
                            price.extra = json.dumps(extra, ensure_ascii=False)
                            price.arrive_time = flown[-1]["arrive_time"]
                            price.flight_no = " / ".join(s["flight_no"] for s in flown)
                            best = price
                    except Exception as e:
                        self.query_stats["failed"] += 1
                        self.logger.warning("[google_flights] %s %s->%s 查询失败: %s", date, from_label, endpoint, e)
                        if self.debug and response is not None:
                            path = Path(self.debug_dir) / f"google_flights_{date}_{origins[0]}.html"
                            try:
                                path.parent.mkdir(parents=True, exist_ok=True)
                                path.write_text(response.text, encoding="utf-8")
                            except OSError:
                                pass
                    self._sleep()
                if best is not None:
                    results.append(best)
                    self.logger.info("[google_flights] %s %s->%s 最低合格报价 ¥%.0f", date, from_label, to_label, best.price)
                else:
                    self.logger.warning("[google_flights] %s %s->%s 无合格报价", date, from_label, to_label)
        return results

    def fetch_routes(self, routes):
        """Batch compatible airport sets; each worker retains its own HTTP client and delay."""
        by_origin = {}
        for route in routes:
            key = (route.from_code, tuple(route.dates), route.alert_group)
            by_origin.setdefault(key, set()).add(route.to_code)
        cohorts = {}
        for (origin, dates, group), targets in by_origin.items():
            cohorts.setdefault((dates, tuple(sorted(targets)), group), []).append(origin)
        size = max(1, min(7, int(self.options.get("origin_batch_size", 7))))
        batches = []
        for (dates, targets, _), origins in cohorts.items():
            origins = sorted(set(origins))
            batches.extend((origins[i:i + size], list(targets), list(dates)) for i in range(0, len(origins), size))
        stats = {"origin_airports": sorted({r.from_code for r in routes}),
                 "destinations": sorted({r.to_code for r in routes}),
                 "batches": len(batches), "completed_batches": 0,
                 "attempted": 0, "parsed": 0, "failed": 0}
        scan_id = datetime.now(timezone.utc).isoformat()

        def fetch_batch(batch):
            crawler = type(self)(self.config, self.logger)
            return crawler.fetch(*batch), crawler.query_stats

        results = []
        workers = max(1, min(4, int(self.options.get("workers", 4))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_batch, batch) for batch in batches]
            for future in as_completed(futures):
                prices, query_stats = future.result()
                for price in prices:
                    extra = json.loads(price.extra)
                    extra["scan_id"] = scan_id
                    price.extra = json.dumps(extra, ensure_ascii=False)
                results.extend(prices)
                stats["completed_batches"] += 1
                for key in ("attempted", "parsed", "failed"):
                    stats[key] += query_stats[key]
                self.logger.info("[全国扫描] 已完成 %d/%d 批次", stats["completed_batches"], len(batches))
        output = Path(".runtime/scan.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_scan_stats = stats
        if stats["attempted"] and not stats["parsed"]:
            raise RuntimeError("全部 Google Flights 查询均失败，不能将本轮视为已完成的价格扫描")
        return results

    @staticmethod
    def _extract_flights(html: str, from_city: str, to_city: str, date: str) -> List[FlightPrice]:
        parser = _FlightDataParser()
        parser.feed(html)
        if not parser.scripts:
            raise ValueError("未找到 Google Flights 航班数据，可能遇到同意页、验证页或页面结构变更")
        script = "".join(parser.scripts)
        _, separator, raw = script.partition("data:")
        if not separator:
            raise ValueError("Google Flights 航班数据格式已变更")
        payload, _ = json.JSONDecoder().raw_decode(raw.lstrip())
        if not isinstance(payload, list) or len(payload) < 4:
            raise ValueError("Google Flights 航班数据格式已变更")

        results = []
        # 第 2、3 区分别为最佳航班和其他航班；两者都参与最低价比较。
        for section in payload[2:4]:
            for item in (section[0] or []) if section else []:
                try:
                    flight, fare = item[0], item[1]
                    price = fare[0][1] if fare and fare[0] else None
                    if isinstance(price, bool) or not isinstance(price, (int, float)):
                        continue
                    if not math.isfinite(price) or price <= 0:
                        continue
                    segments = flight[2]
                    first, last = segments[0], segments[-1]
                    departure_date = "-".join(f"{n:02d}" for n in first[20])
                    if departure_date != date:
                        continue
                    flight_numbers = [
                        str(s[22][0]) + str(s[22][1])
                        for s in segments if len(s) > 22 and s[22] and len(s[22]) > 1
                    ]
                    results.append(FlightPrice(
                        platform="google_flights", from_city=from_city, to_city=to_city,
                        depart_date=date, price=float(price),
                        airline=" / ".join(flight[1] or []),
                        flight_no=" / ".join(flight_numbers),
                        depart_time=GoogleFlightsCrawler._format_time(first[8]),
                        arrive_time=GoogleFlightsCrawler._format_time(last[10]),
                        extra=json.dumps({"segments": [{
                            "from": s[3], "to": s[6], "cabin": s[16],
                            "airline_code": str(s[22][0]) if len(s) > 22 and s[22] else "",
                            "flight_no": str(s[22][0]) + str(s[22][1]) if len(s) > 22 and s[22] else "",
                            "depart_time": GoogleFlightsCrawler._format_time(s[8]),
                            "arrive_time": GoogleFlightsCrawler._format_time(s[10]),
                            "depart_date": "-".join(f"{n:02d}" for n in s[20]),
                            "arrive_date": "-".join(f"{n:02d}" for n in s[21]),
                        } for s in segments]}),
                    ))
                except (IndexError, TypeError, ValueError):
                    # 个别不完整的航班不能导致同一日期的其他有效报价丢失。
                    continue
        return results

    def _matches_filters(self, segments, depart_date):
        if self.max_stops is not None and len(segments) - 1 > self.max_stops:
            return False
        if self.airlines and any(s.get("airline_code", s.get("flight_no", "")[:2]) not in self.airlines for s in segments):
            return False
        if self.verify_cabin:
            expected = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}[self.cabin]
            if any(s.get("cabin") != expected for s in segments):
                return False
        if self.depart_after:
            first = segments[0]
            if first["depart_date"] != depart_date or time.fromisoformat(first["depart_time"]) <= time.fromisoformat(self.depart_after):
                return False
        return True

    @staticmethod
    def _format_time(parts) -> str:
        parts = list(parts or []) + [0, 0]
        return f"{parts[0] or 0:02d}:{parts[1] or 0:02d}"

    @staticmethod
    def _ocean_business(segments) -> bool:
        regions = _airport_regions()
        crossings = []
        for segment in segments:
            origin, destination = regions.get(segment["from"]), regions.get(segment["to"])
            if not origin or not destination:
                return False  # Unknown geography cannot verify the required cabin.
            if (origin in {"NA", "SA"}) != (destination in {"NA", "SA"}):
                crossings.append(segment)
        return bool(crossings) and all(s["cabin"] == 3 for s in crossings)
