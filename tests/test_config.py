"""Generic trip settings, cabin enforcement, and safe template deployment."""
from datetime import date
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
import yaml

from core.config import build_config
from core.models import FlightPrice, Route
from core.alerter import Alerter
from core.storage import PriceStorage
from crawlers.google_flights import GoogleFlightsCrawler
from main import load_config, make_job
from test_google_flights import itinerary, page


ROOT = Path(__file__).resolve().parents[1]
TRIP = {"origin": "JFK", "destination": "LHR", "dates": ["2030-06-15"], "cabin": "business"}
TODAY = date(2030, 1, 1)


class TripConfigurationTests(unittest.TestCase):
    def test_four_fields_enable_cabin_checked_first_and_drop_alerts(self):
        config = build_config(TRIP, TODAY)
        self.assertEqual(config["platforms"], ["google_flights"])
        self.assertEqual([(r["from"], r["to"], r["dates"]) for r in config["routes"]],
                         [("JFK", "LHR", ["2030-06-15"])])
        self.assertTrue(config["routes"][0]["alert_on_drop"])
        self.assertEqual(config["crawler"]["google_flights"]["cabin"], "business")
        self.assertTrue(config["crawler"]["google_flights"]["verify_cabin"])

    def test_normalization_alternatives_and_return_trip(self):
        config = build_config(dict(TRIP, origin=["jfk", "EWR", "JFK"],
                                   dates=[date(2030, 6, 15), "2030-06-15"], return_dates=["2030-06-22"]), TODAY)
        self.assertEqual(len(config["routes"]), 4)
        self.assertEqual({r["from"] for r in config["routes"] if r["dates"] == ["2030-06-15"]}, {"JFK", "EWR"})
        self.assertTrue(all(r["dates"] == ["2030-06-22"] for r in config["routes"] if r["from"] == "LHR"))

    def test_empty_or_expired_dates_do_not_request_flights(self):
        for trip in (dict(TRIP, dates=[]), dict(TRIP, dates=["2000-01-01"])):
            with self.subTest(trip=trip):
                config = build_config(trip, TODAY)
                self.assertEqual(config["routes"], [])
                with patch.object(GoogleFlightsCrawler, "fetch") as fetch:
                    make_job(config, Mock(), Mock(), Mock())()
                    fetch.assert_not_called()

    def test_invalid_or_unverifiable_filters_fail_early(self):
        for changes in ({"origin": None}, {"destination": "JFK"}, {"cabin": "luxury"},
                        {"cabin": []}, {"dates": ["2030-02-30"]}, {"max_stops": True},
                        {"max_stops": -1}, {"depart_after": "25:00"}, {"airlines": ["B"]},
                        {"platforms": ["fliggy"]}, {"unexpected": "setting"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_config(dict(TRIP, **changes), TODAY)

    def test_optional_filters_and_notification_baselines(self):
        config = build_config(dict(TRIP, airlines=["ba"], max_stops=1, depart_after="18:00"), TODAY)
        options = config["crawler"]["google_flights"]
        self.assertEqual((options["airlines"], options["max_stops"], options["depart_after"]), (["BA"], 1, "18:00"))
        baseline = build_config(TRIP, TODAY)["routes"][0]["alert_group"]
        self.assertEqual(baseline, build_config(dict(TRIP, dates=["2030-06-16"]), TODAY)["routes"][0]["alert_group"])
        self.assertNotEqual(baseline, build_config(dict(TRIP, cabin="economy"), TODAY)["routes"][0]["alert_group"])

    def test_cabin_is_checked_before_selecting_lowest_fare(self):
        economy, business = itinerary(100), itinerary(900)
        economy[0][2][0][16], business[0][2][0][16] = 1, 3
        response = httpx.Response(200, text=page(others=[economy, business]),
                                  request=httpx.Request("GET", "https://www.google.com"))
        config = build_config(TRIP, TODAY)["crawler"]
        config.update(delay_min=0, delay_max=0)
        crawler = GoogleFlightsCrawler(config, logging.getLogger("test"))
        with patch("crawlers.google_flights.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            prices = crawler.fetch("JFK", "LHR", ["2030-06-15"])
        self.assertEqual([p.price for p in prices], [900])
        self.assertTrue(json.loads(prices[0].extra)["filters_verified"]["cabin_verified"])

    def test_all_segments_must_match_requested_cabin(self):
        crawler = GoogleFlightsCrawler(build_config(TRIP, TODAY)["crawler"], logging.getLogger("test"))
        self.assertTrue(crawler._matches_filters([{"cabin": 3}, {"cabin": 3}], "2030-06-15"))
        self.assertFalse(crawler._matches_filters([{"cabin": 3}, {"cabin": 1}], "2030-06-15"))

    def test_trip_file_and_actions_configuration_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "trip.yaml"
            path.write_text(yaml.safe_dump(TRIP), encoding="utf-8")
            env = dict(os.environ, SERVERCHAN_KEY="TEST_ONLY_SECRET", GITHUB_OUTPUT=str(folder / "outputs"))
            subprocess.run([sys.executable, str(ROOT / "scripts/prepare_actions_config.py")],
                           cwd=folder, env=env, check=True, capture_output=True)
            runtime = yaml.safe_load((folder / ".runtime/config.yaml").read_text(encoding="utf-8"))
            with patch.dict(os.environ, {"SERVERCHAN_KEY": "TEST_ONLY_SECRET"}):
                self.assertEqual(load_config(str(path)), runtime)
            self.assertIn("ready=true", (folder / "outputs").read_text())
            self.assertNotIn("TEST_ONLY_SECRET", (folder / ".monitor-state/config.json").read_text())
            self.assertEqual(runtime["notifier"]["serverchan"]["send_key"], "TEST_ONLY_SECRET")

    def test_local_scheduler_keeps_future_runs_enabled(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from core.scheduler import run_scheduler
        scheduler = BackgroundScheduler(timezone="UTC")
        try:
            with patch("core.scheduler.BlockingScheduler", return_value=scheduler):
                run_scheduler(Mock(), interval_minutes=90, run_on_start=False)
            self.assertIsNotNone(scheduler.get_job("price_monitor").next_run_time)
        finally:
            scheduler.shutdown(wait=False)

    def test_unchanged_fare_is_not_resent_and_failed_send_does_not_set_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = PriceStorage(str(Path(tmp) / "prices.db"))
            notifier = Mock()
            notifier.send.side_effect = [False, True, True]
            alerter = Alerter(logging.getLogger("test"), notifier=notifier, storage=storage)
            route = Route("JFK", "New York", "LHR", "London", ["2030-06-15"], alert_on_drop=True)
            for price in (900, 900, 900, 800, 850):
                alerter.check_and_alert(route, [FlightPrice("google_flights", "JFK", "LHR", "2030-06-15", price)])
            self.assertEqual(notifier.send.call_count, 3)
            self.assertEqual(storage.get_alert_state("JFK-LHR-2030-06-15"), 800)

    def test_template_has_no_live_itinerary_or_inline_credentials(self):
        settings = yaml.safe_load((ROOT / "trip.yaml").read_text(encoding="utf-8"))
        self.assertEqual(settings["dates"], [])
        workflow = yaml.load((ROOT / ".github/workflows/monitor.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertNotIn("deploy", workflow["jobs"])
        steps = workflow["jobs"]["monitor"]["steps"]
        self.assertFalse(any("secrets." in str(step.get("run", "")) for step in steps))


if __name__ == "__main__":
    unittest.main()
