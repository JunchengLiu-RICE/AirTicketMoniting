"""Local trip editor: real HTTP saves, validation, and preservation of settings."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

import httpx
import yaml

from configure import make_server


class TripEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "trip.yaml"
        self.settings = {"origin": "JFK", "destination": "LHR", "dates": [], "cabin": "economy",
                         "airlines": ["BA"], "max_stops": 1, "depart_after": "18:00"}
        self.path.write_text(yaml.safe_dump(self.settings), encoding="utf-8")
        self.server = make_server(self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.server.server_port}", trust_env=False)
        self.current = self.client.get("/api/trip").json()

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, settings, revision=None, **kwargs):
        return self.client.post("/api/trip", json={"settings": settings, "revision": revision or self.current["revision"]},
                                headers={"X-AirTicket-Token": self.current["token"], **kwargs})

    def test_loading_form_and_catalog_does_not_change_configuration(self):
        before = self.path.read_bytes()
        page = self.client.get("/")
        self.assertIn("设置行程", page.text)
        self.assertEqual(self.client.get("/app.js").status_code, 200)
        airports = self.client.get("/api/airports").json()["airports"]
        self.assertGreater(len(airports), 1000)
        self.assertTrue({"PEK", "PKX", "JFK", "LHR"} <= {a["code"] for a in airports})
        self.assertIn("北京", next(a for a in airports if a["code"] == "PEK")["aliases"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_save_roundtrip_and_alternative_airports_preserves_advanced_filters(self):
        settings = dict(self.current["settings"], origin=["JFK", "EWR"], dates=["2030-06-15", "2030-06-16"],
                        return_dates=["2030-06-22"], cabin="business")
        response = self.post(settings)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["active_routes"], 4)
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8")), settings)
        self.assertEqual(self.client.get("/api/trip").json()["settings"]["airlines"], ["BA"])

    def test_invalid_airports_and_dates_do_not_overwrite_file(self):
        before = self.path.read_bytes()
        for changes in ({"destination": "JFK"}, {"origin": "bad-code"}, {"dates": ["2030-02-30"]},
                        {"cabin": "unknown"}, {"max_stops": -1}, {"notifier": {"send_key": "never-save"}}):
            with self.subTest(changes=changes):
                response = self.post(dict(self.settings, **changes))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.path.read_bytes(), before)

    def test_stale_revision_cannot_overwrite_an_external_change(self):
        modified = dict(self.settings, cabin="business")
        self.path.write_text(yaml.safe_dump(modified), encoding="utf-8")
        response = self.post(self.settings)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8")), modified)

    def test_other_websites_missing_tokens_and_unexpected_paths_are_rejected(self):
        self.assertEqual(self.post(self.settings, Origin="https://example.com").status_code, 403)
        self.assertEqual(self.client.post("/api/trip", json={"settings": self.settings}).status_code, 403)
        self.assertEqual(self.client.get("/trip.yaml").status_code, 404)
        self.assertEqual(self.client.get("/../LICENSE").status_code, 404)
        self.assertEqual(self.client.get("/api/trip", headers={"Host": "example.com"}).status_code, 403)

    def test_empty_dates_can_pause_without_removing_filter_settings(self):
        response = self.post(self.settings)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_routes"], 0)
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8")), self.settings)


if __name__ == "__main__":
    unittest.main()
