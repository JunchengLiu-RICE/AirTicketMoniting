"""Regression coverage for storage batching, report scoping, and crawler cleanup."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from core.models import FlightPrice
from core.storage import PriceStorage
from crawlers.ctrip import CtripCrawler
from crawlers.fliggy import FliggyCrawler
from main import make_job


ROOT = Path(__file__).resolve().parents[1]


class EfficiencyTests(unittest.TestCase):
    def test_fliggy_success_response_is_parsed_once_and_keeps_the_payload(self):
        crawler = FliggyCrawler({}, logging.getLogger("test"))
        payload = {"ret": ["SUCCESS::OK"], "data": {"success": True, "lowestPrice": 900}}
        response = Mock(status_code=200, text=json.dumps(payload))
        with patch("crawlers.fliggy.httpx.Client"), \
             patch.object(crawler, "_mtop_get", return_value=response), \
             patch("crawlers.fliggy.json.loads", wraps=json.loads) as decode:
            ok, raw, blocked = crawler._one_attempt("JFK", "LHR", "2030-06-15")
        self.assertEqual((ok, raw, blocked), (True, payload, False))
        self.assertEqual(decode.call_count, 1)
        for body in ('SUCCESS {', '["SUCCESS"]', '{"ret":["SUCCESS"],"data":[]}'):
            with self.subTest(body=body):
                self.assertIsNone(crawler._priced_response(body))

    def test_fliggy_ignores_invalid_offers_and_uses_valid_lowest_price_fallback(self):
        crawler = FliggyCrawler({"delay_min": 0, "delay_max": 0}, logging.getLogger("test"))
        invalid = (None, "invalid", True, 0, -5, float("inf"), float("nan"))
        offers = [{"flightName": f"DL{i}", "bestPrice": price} for i, price in enumerate(invalid)]
        valid = {"flightName": "DL635", "bestPrice": "850", "depTime": "22:25"}
        offers.extend([valid, valid, {"flightName": "DL123", "bestPrice": 900}])
        raw = {"data": {"items": [{"itemType": "DIRECT", "itemDatas": offers}]}}
        with patch.object(crawler, "_hoop", return_value=raw):
            prices = crawler.fetch("JFK", "LHR", ["2030-06-15"])
        self.assertEqual((prices[0].price, prices[0].flight_no), (850, "DL635"))
        self.assertEqual(len(crawler._parse_offers(raw)), 2)
        for amount in (*invalid, "900"):
            with self.subTest(amount=amount), patch.object(crawler, "_hoop", return_value={"data": {"lowestPrice": amount}}):
                prices = crawler.fetch("JFK", "LHR", ["2030-06-15"])
                self.assertEqual([p.price for p in prices], [900] if amount == "900" else [])

    def test_batch_insert_is_one_transaction_and_rolls_back_partial_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = PriceStorage(str(Path(tmp) / "prices.db"))
            statements = []
            connect = storage._conn

            @contextmanager
            def traced():
                with connect() as con:
                    con.set_trace_callback(statements.append)
                    yield con

            with patch.object(storage, "_conn", traced):
                storage.save_many(FlightPrice("ctrip", "JFK", "LHR", "2030-06-15", p)
                                  for p in range(500, 600))
            self.assertEqual(sum(s == "COMMIT" for s in statements), 1)
            self.assertEqual(storage.last_lowest("JFK", "LHR", "2030-06-15")["price"], 500)
            # A failing row cannot leave the first, cheaper row partially committed.
            with self.assertRaises(sqlite3.IntegrityError):
                storage.save_many([FlightPrice("ctrip", "JFK", "LHR", "2030-06-15", 100),
                                   FlightPrice("ctrip", "JFK", "LHR", "2030-06-15", None)])
            self.assertEqual(storage.last_lowest("JFK", "LHR", "2030-06-15")["price"], 500)

    def test_ctrip_reuses_one_listener_without_reusing_previous_date_prices(self):
        crawler = CtripCrawler({"delay_min": 0, "delay_max": 0}, logging.getLogger("test"))
        page = Mock()
        listeners = []
        page.on.side_effect = lambda event, listener: listeners.append(listener)
        amounts = iter((700, 900))

        def navigate(*args, **kwargs):
            response = Mock()
            response.url = "https://m.ctrip.com/flightListSearchForH5"
            response.text.return_value = json.dumps({"fltitem": [
                {"policyinfo": [{"tprice": next(amounts), "quantity": 2}]}]})
            for listener in listeners:
                listener(response)

        page.goto.side_effect = navigate
        with patch.object(crawler, "browser"), patch.object(crawler, "new_page", return_value=page):
            prices = crawler.fetch("JFK", "LHR", ["2030-06-14", "2030-06-15"])
        self.assertEqual([p.price for p in prices], [700, 900])
        self.assertEqual(len(listeners), 1)

    def test_one_shot_startup_does_not_import_browser_or_scheduler(self):
        subprocess.run([sys.executable, "-c",
                        "import sys; import main; "
                        "assert not any(k.startswith(('playwright', 'apscheduler')) for k in sys.modules)"],
                       cwd=ROOT, check=True, capture_output=True)

    def test_snapshot_scopes_timestamp_minima_and_history_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            storage = PriceStorage(str(folder / "prices.db"))
            now = datetime.now()
            for origin, price, stamp, extra in (
                ("JFK", 800, now - timedelta(hours=2), '{"scan_id":"z-first"}'),
                ("JFK", 900, now - timedelta(hours=1, minutes=50), '{"scan_id":"a-later"}'),
                # An unrelated route must not move the report window or have its JSON decoded.
                ("CTU", 1, now, 'invalid unrelated JSON'),
            ):
                storage.save(FlightPrice("google_flights", origin, "LHR", "2030-06-15", price,
                                         fetched_at=stamp.strftime("%Y-%m-%d %H:%M:%S"), extra=extra))
            config = folder / "config.yaml"
            output = folder / "latest.json"
            config.write_text(yaml.safe_dump({"platforms": ["google_flights"], "routes": [
                {"from": "JFK", "to": "LHR", "dates": ["2030-06-15"]}]}), encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/export_results.py"), "--db", storage.db_path,
                       "--config", str(config), "--out", str(output)]
            subprocess.run(command, check=True, capture_output=True)
            data = json.loads(output.read_text(encoding="utf-8"))["data"]
            self.assertEqual([r["price"] for r in data["results"]], [800, 900])
            self.assertEqual(data["summary"][0]["per_platform"], {"google_flights": 800})
            self.assertEqual([p["v"] for p in data["trend"]["2030-06-15"]["google_flights"]], [800, 900])
            config.write_text("platforms: [google_flights]\nroutes: []\n", encoding="utf-8")
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["data"]["results"], [])

    def test_batch_prices_are_associated_only_with_the_route_requested_dates(self):
        logger, storage, alerter = Mock(), Mock(), Mock()
        config = {"platforms": ["google_flights"], "crawler": {"google_flights": {"origin_batch_size": 7}},
                  "routes": [{"from": "JFK", "to": "LHR", "dates": [d]}
                             for d in ("2030-06-14", "2030-06-15")]}
        prices = [FlightPrice("google_flights", "JFK", "LHR", d, 800)
                  for d in ("2030-06-14", "2030-06-15")]
        with patch("crawlers.google_flights.GoogleFlightsCrawler.fetch_routes", return_value=prices):
            make_job(config, logger, storage, alerter)()
        self.assertEqual([[p.depart_date for p in call.args[1]] for call in alerter.check_and_alert.call_args_list],
                         [["2030-06-14"], ["2030-06-15"]])


if __name__ == "__main__":
    unittest.main()
