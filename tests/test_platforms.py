"""Platform removal, multi-source comparison, and report migration coverage."""
import json
import logging
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from core.alerter import Alerter
from core.models import FlightPrice
from core.platforms import DEFAULT_PLATFORMS, validate_platforms
from core.storage import PriceStorage
from crawlers import REGISTRY
from main import make_job
from core.config import build_config


ROOT = Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    def test_removed_platforms_rejected_before_requests(self):
        for platform in ("tuniu", "qunar", "tongcheng"):
            with self.subTest(platform=platform):
                with self.assertRaises(ValueError):
                    build_config({"origin": "JFK", "destination": "LHR", "dates": [], "cabin": "economy", "platforms": ["ctrip", platform]})
                with self.assertRaises(ValueError):
                    make_job({"platforms": [platform]}, Mock(), Mock(), Mock())

    def test_validation_and_default_config_match_available_crawlers(self):
        self.assertEqual(set(DEFAULT_PLATFORMS), set(REGISTRY))
        self.assertEqual(validate_platforms(["fliggy", "ctrip", "fliggy"]), ["fliggy", "ctrip"])
        for platforms in ([], "ctrip", [None], [["ctrip"]]):
            with self.subTest(platforms=platforms), self.assertRaises(ValueError):
                validate_platforms(platforms)
        config = build_config({"origin": "JFK", "destination": "LHR", "dates": [], "cabin": "economy", "platforms": list(DEFAULT_PLATFORMS)})
        self.assertEqual(config["platforms"], list(DEFAULT_PLATFORMS))

    def test_all_three_sources_feed_storage_and_lowest_fare_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = PriceStorage(str(Path(tmp) / "prices.db"))
            notifier = Mock()
            notifier.send.return_value = True
            logger = logging.getLogger("platform_tests")
            alerter = Alerter(logger, notifier=notifier, storage=storage)
            cfg = {"routes": [{"from": "JFK", "to": "LHR", "dates": ["2030-06-15"],
                               "alert_on_drop": True}]}
            # Omitting platforms uses all three supported sources.
            prices = [FlightPrice(p, "JFK", "LHR", "2030-06-15", price)
                      for p, price in zip(DEFAULT_PLATFORMS, (900, 800, 850))]
            with patch.object(REGISTRY["fliggy"], "fetch", return_value=[prices[0]]) as fliggy, \
                 patch.object(REGISTRY["ctrip"], "fetch", return_value=[prices[1]]) as ctrip, \
                 patch.object(REGISTRY["google_flights"], "fetch", return_value=[prices[2]]) as google:
                make_job(cfg, logger, storage, alerter)()
            for source in (fliggy, ctrip, google):
                source.assert_called_once_with("JFK", "LHR", ["2030-06-15"])
            self.assertEqual(storage.last_lowest("JFK", "LHR", "2030-06-15")["price"], 800)
            self.assertEqual(storage.get_alert_state("JFK-LHR-2030-06-15"), 800)
            notifier.send.assert_called_once()
            self.assertIn("m.ctrip.com", notifier.send.call_args.args[1])

    def test_reports_exclude_retired_and_unselected_platforms_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            storage = PriceStorage(str(folder / "prices.db"))
            now = datetime.now()
            for platform, amount, stamp in (("google_flights", 800, now - timedelta(hours=1)),
                                            ("ctrip", 700, now), ("qunar", 1, now)):
                storage.save(FlightPrice(platform, "JFK", "LHR", "2030-06-15", amount,
                                        fetched_at=stamp.strftime("%Y-%m-%d %H:%M:%S")))
            command = [sys.executable, str(ROOT / "scripts/export_results.py"),
                       "--db", storage.db_path, "--out", str(folder / "latest.json")]
            config = folder / "config.yaml"
            config.write_text(yaml.safe_dump({"platforms": ["google_flights"], "routes": [
                {"from": "JFK", "to": "LHR", "dates": ["2030-06-15"]}]}), encoding="utf-8")
            subprocess.run([*command, "--config", str(config)], check=True, capture_output=True)
            data = json.loads((folder / "latest.json").read_text(encoding="utf-8"))["data"]
            self.assertEqual(data["query"]["platforms"], ["google_flights"])
            self.assertEqual(data["summary"][0]["min_price"], 800)
            self.assertEqual(set(data["trend"]["2030-06-15"]), {"google_flights"})
            subprocess.run(command, check=True, capture_output=True)
            data = json.loads((folder / "latest.json").read_text(encoding="utf-8"))["data"]
            self.assertEqual(data["query"]["platforms"], ["ctrip"])
            # The historical low remains in SQLite; only exports exclude it.
            self.assertEqual(storage.last_lowest("JFK", "LHR", "2030-06-15")["price"], 1)


if __name__ == "__main__":
    unittest.main()
