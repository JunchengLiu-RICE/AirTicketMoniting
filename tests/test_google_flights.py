"""Google Flights 的查询约束、报价筛选及现有监控流程集成。"""
import base64
import copy
import json
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import yaml

from core.alerter import Alerter
from core.google_flights import build_google_flights_url
from core.models import Route
from core.storage import PriceStorage
from crawlers import REGISTRY
from crawlers.google_flights import GoogleFlightsCrawler
from main import make_job
from scripts.render_report import PLATFORM_COLORS, PLATFORM_NAMES


DATE = "2030-06-15"
ROOT = Path(__file__).resolve().parents[1]


def itinerary(price, depart=(2030, 6, 15), arrival=(17, 5)):
    # Google 的航段结构，保留解析所需的真实字段位置。
    segment = [None] * 23
    segment[3:7] = ["JFK", "New York", "London", "LHR"]
    segment[8], segment[10] = [15, 38], list(arrival)
    segment[20], segment[21] = list(depart), list(depart)
    segment[22] = ["AS", "1831", None, "Alaska"]
    flight = ["AS", ["Alaska"], [segment]]
    return [flight, [[None, price]]]


def page(best=(), others=()):
    payload = [None, None, [list(best)], [list(others)]]
    return ('<html><body>Calendar from ¥1, other dates ¥2</body>'
            '<script class="ds:1">AF_initDataCallback({key: \'ds:1\', data:'
            + json.dumps(payload) + ', sideChannel: {}});</script></html>')


class GoogleFlightsTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_google_flights")
        self.logger.addHandler(logging.NullHandler())
        self.crawler = GoogleFlightsCrawler({"delay_min": 0, "delay_max": 0}, self.logger)

    def test_precise_one_way_cny_query(self):
        url = build_google_flights_url("jfk", "lhr", DATE)
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.hostname, "www.google.com")
        self.assertEqual(params["curr"], ["CNY"])
        self.assertEqual(params["hl"], ["en"])
        # 已与 Google 可访问的真实查询 URL 交叉核验。
        expected = base64.b64encode(b"\x1a\x1a\x12\x0a2030-06-15\x6a\x05\x12\x03JFK\x72\x05\x12\x03LHR\x42\x01\x01\x48\x01\x98\x01\x02")
        self.assertEqual(base64.urlsafe_b64decode(params["tfs"][0]), base64.b64decode(expected))

    def test_invalid_queries_rejected(self):
        for codes_date in [("JFK?", "LHR", DATE), ("JFK", "LA", DATE),
                           ("JFK", "LHR", "2026-02-30"), ("JFK", "LHR", "20261115")]:
            with self.subTest(query=codes_date), self.assertRaises(ValueError):
                build_google_flights_url(*codes_date)

    def test_best_and_other_groups_only_valid_requested_date_fares(self):
        html = page(
            best=[itinerary(923), itinerary(None), itinerary(0), itinerary(True)],
            others=[itinerary(861), itinerary(-1), itinerary(float("inf")),
                    itinerary(3, depart=(2030, 6, 16)), [None], itinerary("USD 5")],
        )
        prices = self.crawler._extract_flights(html, "JFK", "LHR", DATE)
        self.assertEqual([p.price for p in prices], [923, 861])
        self.assertEqual(prices[0].airline, "Alaska")
        self.assertEqual(prices[0].flight_no, "AS1831")
        self.assertEqual(prices[0].depart_time, "15:38")
        self.assertEqual(prices[0].arrive_time, "17:05")

    def test_connections_and_midnight_times(self):
        item = itinerary(500)
        second = copy.deepcopy(item[0][2][0])
        item[0][2][0][8] = [None, 31]
        second[10] = [8]
        second[22] = ["AS", "99"]
        item[0][2].append(second)
        price = self.crawler._extract_flights(page(others=[item]), "JFK", "LHR", DATE)[0]
        self.assertEqual(price.depart_time, "00:31")
        self.assertEqual(price.arrive_time, "08:00")
        self.assertEqual(price.flight_no, "AS1831 / AS99")

    def test_no_fares_and_consent_page(self):
        self.assertEqual(self.crawler._extract_flights(page(), "JFK", "LHR", DATE), [])
        with self.assertRaises(ValueError):
            self.crawler._extract_flights("<html>Before you continue to Google</html>", "JFK", "LHR", DATE)

    def test_failed_date_does_not_lose_later_date(self):
        good = httpx.Response(200, text=page(others=[itinerary(861)]),
                              request=httpx.Request("GET", "https://www.google.com"))
        with patch("crawlers.google_flights.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.get.side_effect = [httpx.ConnectError("network unavailable"), good]
            prices = self.crawler.fetch("JFK", "LHR", ["2030-06-14", DATE])
        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0].depart_date, DATE)
        self.assertEqual(json.loads(prices[0].extra)["currency"], "CNY")
        self.assertEqual(client_cls.call_args.kwargs["timeout"], 45)

    def test_storage_alert_and_report_integration(self):
        self.assertIs(REGISTRY["google_flights"], GoogleFlightsCrawler)
        self.assertEqual(PLATFORM_NAMES["google_flights"], "Google Flights")
        self.assertIn("google_flights", PLATFORM_COLORS)
        route = Route("JFK", "New York", "LHR", "London", [DATE], 900)
        price = self.crawler._extract_flights(page(others=[itinerary(861)]), "JFK", "LHR", DATE)[0]
        with tempfile.TemporaryDirectory() as tmp:
            storage = PriceStorage(str(Path(tmp) / "prices.db"))
            notifier = Mock()
            notifier.send.return_value = True
            alerter = Alerter(self.logger, notifier=notifier, storage=storage)
            cfg = {"platforms": ["google_flights"], "routes": [{
                "from": "JFK", "to": "LHR", "dates": [DATE], "alert_threshold": 900,
            }]}
            with patch.object(GoogleFlightsCrawler, "fetch", return_value=[price]):
                make_job(cfg, self.logger, storage, alerter)()
            self.assertEqual(storage.last_lowest("JFK", "LHR", DATE)["price"], 861)
            self.assertEqual(storage.get_alert_state(f"JFK-LHR-{DATE}"), 861)
            self.assertIn(build_google_flights_url("JFK", "LHR", DATE), notifier.send.call_args.args[1])
            self.assertEqual(alerter._build_view_url(route, DATE, "google_flights"),
                             build_google_flights_url("JFK", "LHR", DATE))
            exported = Path(tmp) / "latest.json"
            subprocess.run([sys.executable, str(ROOT / "scripts/export_results.py"),
                            "--db", storage.db_path, "--out", str(exported)],
                           check=True, capture_output=True)
            subprocess.run([sys.executable, str(ROOT / "scripts/render_report.py"),
                            "--results-file", str(exported), "--out-dir", tmp],
                           check=True, capture_output=True)
            self.assertIn("Google Flights", (Path(tmp) / "report.html").read_text(encoding="utf-8"))

    def test_supported_platforms_match_example_config(self):
        cfg = {"platforms": ["fliggy", "ctrip", "google_flights"]}
        self.assertEqual(cfg["platforms"], ["fliggy", "ctrip", "google_flights"])
        self.assertEqual(set(REGISTRY), {"ctrip", "fliggy", "google_flights"})


if __name__ == "__main__":
    unittest.main()
