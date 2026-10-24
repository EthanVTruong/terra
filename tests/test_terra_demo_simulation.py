"""Meaningful offline demo checks against the actual frozen QC and orchard policy."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from chillwatch.estimate import estimate_location
from terra_monitoring.demo_simulation.engine import DATA, START, DemoSimulation
from terra_monitoring.demo_simulation.server import create_app
from terra_monitoring.farm_ops.store import utc


class DemoSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "isolated-demo"
        self.demo = DemoSimulation(state_dir=self.directory, grid_size=2)

    def test_original_and_minor_readings_produce_no_fault_or_phone_alert(self):
        baseline = self.demo.state()
        self.assertEqual(baseline["clock"]["current_utc"], START)
        self.assertTrue(all(s["assessment"]["available"] for s in baseline["stations"]))
        self.assertTrue(all(s["assessment"]["level"] == "none" for s in baseline["stations"]))
        self.assertAlmostEqual(baseline["assessment"]["location_estimate"]["temperature"], 47.57816060736636)
        minor = self.demo.action({"type": "preset", "name": "minor"})
        self.assertEqual(minor["assessment"]["station_findings"], [])
        self.assertEqual(minor["notifications"], [])
        self.assertEqual(len(minor["messages"]), 1)  # Actual monitor's initial welcome.
        davis = next(s for s in minor["stations"] if s["id"] == 6)
        self.assertEqual(davis["value_f"], davis["original_f"] + 1)

    def test_spike_runs_real_qc_and_preserves_supported_orchard(self):
        state = self.demo.action({"type": "preset", "name": "spike"})
        finding, = state["assessment"]["station_findings"]
        self.assertEqual((finding["station_id"], finding["severity"]), (6, "strong"))
        davis = next(s for s in state["stations"] if s["id"] == 6)
        self.assertTrue(davis["flagged"])
        self.assertEqual(davis["assessment"]["level"], "strong")
        self.assertAlmostEqual(davis["assessment"]["residual_f"], 25.075)
        estimate = state["assessment"]["location_estimate"]
        self.assertEqual(estimate["status"], "ok")
        self.assertNotIn(6, estimate["used_station_ids"])
        self.assertEqual(state["notifications"], [])
        self.assertFalse(any(e["type"] == "monitoring_limited" for e in state["events"]))

    def test_support_loss_is_real_grower_event_and_repeated_hour_deduplicates(self):
        first = self.demo.action({"type": "preset", "name": "support_loss"})
        self.assertEqual(first["assessment"]["location_estimate"]["status"], "insufficient_data")
        self.assertTrue(all(c["temperature_f"] is None for c in first["heatmap"]["cells"]))
        notification, = first["notifications"]
        message = next(m for m in first["messages"] if m["id"] == notification["id"])
        self.assertEqual(notification["text"], message["text"])
        self.assertEqual(notification["event_types"], ["monitoring_limited"])
        self.assertEqual(notification["status"], "simulated")
        self.assertFalse(notification["provider_delivery"])
        again = self.demo.action({"type": "preset", "name": "support_loss"})
        self.assertEqual([m["id"] for m in again["messages"]], [m["id"] for m in first["messages"]])
        self.assertEqual(again["notifications"], first["notifications"])

    def test_recovery_requires_three_distinct_hourly_snapshots(self):
        limited = self.demo.action({"type": "preset", "name": "support_loss"})
        self.assertEqual(limited["monitor"]["demo_health"], {
            "incident_active": True, "consecutive_good_readings": 0, "required_good_readings": 3})
        for _ in range(3):
            same_hour = self.demo.action({"type": "preset", "name": "normal"})
        self.assertFalse(any(e["type"] == "monitoring_recovered" for e in same_hour["events"]))
        # The current monitor excludes the last unavailable hour even after a
        # same-hour revision. Only subsequent distinct hours prove recovery.
        self.assertEqual(same_hour["monitor"]["demo_health"]["consecutive_good_readings"], 0)
        self.assertTrue(same_hour["monitor"]["demo_health"]["incident_active"])
        next_hour = self.demo.action({"type": "advance", "hours": 1})
        self.assertFalse(any(e["type"] == "monitoring_recovered" for e in next_hour["events"]))
        self.assertEqual(next_hour["monitor"]["demo_health"]["consecutive_good_readings"], 1)
        self.assertTrue(next_hour["monitor"]["demo_health"]["incident_active"])
        second_hour = self.demo.action({"type": "advance", "hours": 1})
        self.assertFalse(any(e["type"] == "monitoring_recovered" for e in second_hour["events"]))
        self.assertEqual(second_hour["monitor"]["demo_health"]["consecutive_good_readings"], 2)
        third = self.demo.action({"type": "advance", "hours": 1})
        self.assertEqual(sum(e["type"] == "monitoring_recovered" for e in third["events"]), 1)
        self.assertEqual(len(third["notifications"]), 2)
        self.assertEqual(utc(third["clock"]["current_utc"]), utc(START) + timedelta(hours=3))
        self.assertEqual(third["monitor"]["demo_health"], {
            "incident_active": False, "consecutive_good_readings": 3, "required_good_readings": 3})

    def test_note_snapshot_preserves_full_first_publication_context_after_edits_and_restart(self):
        first = self.demo.action({"type": "preset", "name": "support_loss"})
        note_id = first["notifications"][0]["id"]
        snapshot = self.demo.details(note_id)["demo_snapshot"]
        self.assertEqual(snapshot["timestamp"], START)
        self.assertEqual(snapshot["stations"], first["stations"])
        self.assertEqual(snapshot["history"], first["history"])
        self.assertEqual(snapshot["assessment"], first["assessment"])
        self.assertEqual(sum(not s["online"] for s in snapshot["stations"]), 3)
        self.demo.action({"type": "preset", "name": "normal"})
        altered = self.demo.action({"type": "temperature", "station_id": 6, "value_f": 90})
        self.assertNotEqual(altered["stations"], snapshot["stations"])
        self.assertNotEqual(altered["history"], snapshot["history"])
        self.assertEqual(self.demo.details(note_id)["demo_snapshot"], snapshot)
        resumed = DemoSimulation(state_dir=self.directory, grid_size=2)
        self.assertEqual(resumed.details(note_id)["demo_snapshot"], snapshot)
        self.assertEqual(resumed.details(note_id)["assessments"][0], snapshot["assessment"])

    def test_answer_snapshot_is_frozen_without_computing_an_intermediate_heatmap(self):
        # The initial map is uncomputed. Note capture must not call state(),
        # which would eagerly create the grid at every intermediate replay hour.
        with patch.object(self.demo, "_heatmap", side_effect=AssertionError("snapshot rendered a map")):
            self.demo._advance(2)
        reply = self.demo.question("Is weather support usable?", request_id="frozen-question")
        note_id = reply["message"]["id"]
        snapshot = self.demo.details(note_id)["demo_snapshot"]
        self.assertEqual(snapshot["timestamp"], reply["state"]["clock"]["current_utc"])
        self.demo.action({"type": "temperature", "station_id": 6, "value_f": 90})
        self.demo.question("Is weather support usable?", request_id="frozen-question")
        self.assertEqual(self.demo.details(note_id)["demo_snapshot"], snapshot)

    def test_recover_preset_advances_three_new_hours_and_retains_incident_evidence(self):
        first = self.demo.action({"type": "preset", "name": "support_loss"})
        opening_id = first["notifications"][0]["id"]
        recovered = self.demo.action({"type": "preset", "name": "recover"})
        self.assertEqual(utc(recovered["clock"]["current_utc"]), utc(START) + timedelta(hours=3))
        self.assertTrue(any(e["type"] == "monitoring_recovered" for e in recovered["events"]))
        details = self.demo.details(opening_id)
        self.assertEqual(details["assessments"][0]["query"]["valid_utc"], START)
        self.assertEqual(details["assessments"][0]["location_estimate"]["status"], "insufficient_data")
        self.assertEqual(recovered["assessment"]["location_estimate"]["status"], "ok")

    def test_temperature_is_one_hour_while_offline_persists_until_restored(self):
        first = self.demo.action({"type": "temperature", "station_id": 6, "value_f": 75})
        self.assertEqual(next(s for s in first["stations"] if s["id"] == 6)["value_f"], 75)
        self.demo.action({"type": "online", "station_id": 139, "online": False})
        advanced = self.demo.action({"type": "advance", "hours": 1})
        davis = next(s for s in advanced["stations"] if s["id"] == 6)
        self.assertEqual(davis["value_f"], davis["original_f"])
        winters = next(s for s in advanced["stations"] if s["id"] == 139)
        self.assertFalse(winters["online"])
        self.assertIsNone(winters["value_f"])
        self.assertFalse(winters["assessment"]["available"])
        self.assertIsNotNone(winters["original_f"])

    def test_reset_temperatures_restores_recorded_readings_but_keeps_reporting(self):
        self.demo.action({"type": "temperature", "station_id": 6, "value_f": 75})
        self.demo.action({"type": "online", "station_id": 139, "online": False})
        state = self.demo.action({"type": "reset_temperatures"})
        davis = next(s for s in state["stations"] if s["id"] == 6)
        self.assertEqual(davis["value_f"], davis["original_f"])
        self.assertIsNone(davis["override"])
        self.assertFalse(next(s for s in state["stations"] if s["id"] == 139)["online"])
        with self.assertRaises(ValueError):
            self.demo.action({"type": "reset_temperatures", "station_id": 6})

    def test_test_sensor_feeds_estimates_but_never_frozen_station_qc(self):
        baseline = self.demo.state()["assessment"]["location_estimate"]["temperature"]
        placed = self.demo.action({"type": "sensor", "lat": 38.56, "lon": -121.79, "value_f": 70})
        sensor = placed["sensors"][0]
        self.assertFalse(sensor["calibrated"])
        self.assertNotIn(sensor["id"], {s["id"] for s in placed["stations"]})
        estimate = placed["assessment"]["location_estimate"]
        self.assertIn(sensor["id"], estimate["used_station_ids"])
        self.assertGreater(estimate["temperature"], baseline)
        self.assertTrue(self.demo.scored.station_id.isin(self.demo.station_ids).all())
        spot = self.demo.point(38.56, -121.79)
        self.assertIn(sensor["id"], spot["layers"]["filtered"]["used_station_ids"])
        warmer = self.demo.action({"type": "sensor_value", "sensor_id": sensor["id"], "value_f": 90})
        self.assertGreater(warmer["assessment"]["location_estimate"]["temperature"], estimate["temperature"])
        removed = self.demo.action({"type": "sensor_remove", "sensor_id": sensor["id"]})
        self.assertEqual(removed["sensors"], [])
        self.assertAlmostEqual(removed["assessment"]["location_estimate"]["temperature"], baseline)
        self.demo.action({"type": "sensor", "lat": 38.56, "lon": -121.79, "value_f": 70})
        self.assertEqual(self.demo.action({"type": "reset_temperatures"})["sensors"], [])
        for action in ({"type": "sensor", "lat": 45.0, "lon": -121.79, "value_f": 70},
                       {"type": "sensor", "lat": 38.56, "lon": -121.79, "value_f": 200},
                       {"type": "sensor_value", "sensor_id": 6, "value_f": 50},
                       {"type": "sensor_remove", "sensor_id": True}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.demo.action(action)

    def test_out_of_range_test_sensor_is_flagged_and_texted_once(self):
        normal = self.demo.action({"type": "sensor", "lat": 38.56, "lon": -121.79, "value_f": 48})
        sensor = normal["sensors"][0]
        self.assertEqual(sensor["check"]["level"], "none")
        self.assertEqual(normal["notifications"], [])
        limits = self.demo._sensor_limits()
        far = sensor["check"]["expected_f"] + limits["extreme_f"] + 5
        flagged = self.demo.action({"type": "sensor_value", "sensor_id": sensor["id"], "value_f": far})
        check = flagged["sensors"][0]["check"]
        self.assertEqual(check["level"], "strong")
        self.assertGreater(check["deviation_f"], limits["extreme_f"])
        texts = [n for n in flagged["notifications"] if "sensor_flag" in n["event_types"]]
        self.assertEqual(len(texts), 1)
        self.assertIn("local sensor read", texts[0]["text"])
        self.assertIn("predict", texts[0]["text"])
        # The same open incident does not text again on the next hour.
        later = self.demo.action({"type": "advance", "hours": 1})
        self.assertEqual(len([n for n in later["notifications"] if "sensor_flag" in n["event_types"]]), 1)
        # Back inside the range closes the incident quietly; a new excursion is a new text.
        self.demo.action({"type": "sensor_value", "sensor_id": sensor["id"], "value_f": later["sensors"][0]["check"]["expected_f"]})
        again = self.demo.action({"type": "sensor_value", "sensor_id": sensor["id"], "value_f": far})
        self.assertEqual(len([n for n in again["notifications"] if "sensor_flag" in n["event_types"]]), 2)

    def test_moving_the_orchard_pin_starts_a_fresh_watch_there(self):
        before = self.demo.state()
        moved = self.demo.action({"type": "orchard", "lat": 38.7, "lon": -121.7})
        self.assertNotEqual(moved["orchard"]["id"], before["orchard"]["id"])
        self.assertTrue(moved["orchard"]["id"].startswith("sim-"))
        self.assertEqual((moved["orchard"]["lat"], moved["orchard"]["lon"]), (38.7, -121.7))
        self.assertEqual(moved["assessment"]["query"]["lat"], 38.7)
        self.assertEqual([m["event_types"] for m in moved["messages"]], [["welcome"]])
        with self.assertRaises(ValueError):
            self.demo.action({"type": "orchard", "lat": 10, "lon": -121.7})

    def test_spot_estimate_is_native_estimator_and_read_only(self):
        spiked = self.demo.action({"type": "preset", "name": "spike"})
        spot = self.demo.point(38.6, -121.9)
        expected = estimate_location(38.6, -121.9, 60, [START], START,
            self.demo.cleaned, self.demo.stations, None, location_id="comparison",
            geometry_source="test", max_age_hours=6).iloc[0]
        self.assertAlmostEqual(spot["layers"]["raw"]["temperature_f"], expected.temperature)
        self.assertNotIn(6, spot["layers"]["filtered"]["used_station_ids"])
        self.assertEqual(self.demo.state(), spiked)
        for lat, lon in ((40.0, -121.9), (38.6, float("nan")), (True, -121.9)):
            with self.subTest(lat=lat, lon=lon), self.assertRaises(ValueError):
                self.demo.point(lat, lon)

    def test_heatmap_layers_are_exact_native_estimates_and_cache_on_poll(self):
        state = self.demo.action({"type": "preset", "name": "spike"})
        raw = state["heatmap"]["layers"]["raw"][0]
        expected = estimate_location(raw["lat"], raw["lon"], 60, [START], START,
            self.demo.cleaned, self.demo.stations, None, location_id="comparison",
            geometry_source="test", max_age_hours=6).iloc[0]
        self.assertAlmostEqual(raw["temperature_f"], expected.temperature)
        filtered = state["heatmap"]["layers"]["filtered"]
        self.assertTrue(any(abs(a["temperature_f"] - b["temperature_f"]) > .1
                            for a, b in zip(state["heatmap"]["layers"]["raw"], filtered)))
        self.assertTrue(all(6 not in cell["used_station_ids"] for cell in filtered))
        with patch("terra_monitoring.demo_simulation.engine.estimate_location", side_effect=AssertionError("poll recomputed map")):
            self.assertEqual(self.demo.state()["heatmap"], state["heatmap"])

    def test_immutable_source_and_holdout_provenance(self):
        source = DATA / "cimis-march-09-15-2026.json"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(digest, "d84985213d73707dc54a6034256f73a8738fb21ccd64605141fd0a7ed48f2385")
        before = deepcopy(self.demo._originals)
        self.demo.action({"type": "preset", "name": "spike"})
        self.demo.action({"type": "preset", "name": "support_loss"})
        self.assertEqual(self.demo._originals, before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertTrue(all(utc(r["timestamp"]) >= utc(self.demo.provenance["calibration_end_exclusive"])
                            for r in before))
        self.assertTrue(self.demo.state()["source"]["no_training_overlap"])

    def test_restart_preserves_run_messages_and_overrides_without_live_state(self):
        first = self.demo.action({"type": "preset", "name": "support_loss"})
        resumed = DemoSimulation(state_dir=self.directory, grid_size=2).state()
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertEqual(resumed["revision"], first["revision"])
        self.assertEqual(resumed["notifications"], first["notifications"])
        self.assertEqual([s["online"] for s in resumed["stations"]], [s["online"] for s in first["stations"]])
        new = self.demo.action({"type": "reset"})
        self.assertNotEqual(new["run_id"], first["run_id"])
        self.assertEqual(new["clock"]["current_utc"], START)
        self.assertEqual(new["notifications"], [])
        self.assertEqual(len(new["messages"]), 1)

    def test_no_live_environment_credentials_or_network_are_used(self):
        live_path = Path(self.temp.name) / "must-not-touch-live.sqlite3"
        with patch.dict(os.environ, {"TERRA_FARM_DB": str(live_path), "OPENAI_API_KEY": "unusable-test-value",
                                     "TERRA_LLM_MODEL": "never-call"}), \
             patch("httpx.Client.send", side_effect=AssertionError("unexpected network")):
            isolated = DemoSimulation(state_dir=Path(self.temp.name) / "other-demo", grid_size=2)
            result = isolated.action({"type": "preset", "name": "support_loss"})
            self.assertEqual(result["notifications"][0]["status"], "simulated")
            self.assertIsNone(isolated.monitor.delivery)
            self.assertIsNone(isolated.monitor.source)
            self.assertEqual(isolated.monitor.writer.key, "")
        self.assertFalse(live_path.exists())

    def test_invalid_actions_are_rejected_without_advancing(self):
        before = self.demo.state()
        for action in ({"type": "temperature", "station_id": 6, "value_f": True},
                       {"type": "temperature", "station_id": 6, "value_f": float("nan")},
                       {"type": "online", "station_id": 6, "online": "false"},
                       {"type": "online", "station_id": 999, "online": False},
                       {"type": "advance", "hours": 0}, {"type": "advance", "hours": True},
                       {"type": "preset", "name": "unknown"}, {"type": "reset", "recipient": "none"}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.demo.action(action)
        self.assertEqual(self.demo.state(), before)

    def test_http_actions_questions_exact_details_and_origin_guard(self):
        with TestClient(create_app(simulation=self.demo)) as client:
            baseline = client.get("/api/demo/state")
            self.assertEqual(baseline.status_code, 200)
            self.assertEqual(baseline.headers["cache-control"], "no-store")
            result = client.post("/api/demo/action", json={"type": "preset", "name": "support_loss"})
            self.assertEqual(result.status_code, 200)
            message_id = result.json()["notifications"][0]["id"]
            details = client.get(f"/api/demo/messages/{message_id}/details")
            self.assertEqual(details.json()["message"]["id"], message_id)
            self.assertEqual(details.json()["assessments"][0]["query"]["valid_utc"], START)
            answer = client.post("/api/demo/questions", json={"text": "Explain this alert", "request_id": "q-one"})
            self.assertEqual(answer.status_code, 200)
            repeated = client.post("/api/demo/questions", json={"text": "Explain this alert", "request_id": "q-one"})
            self.assertEqual(answer.json()["message"]["id"], repeated.json()["message"]["id"])
            self.assertEqual(len(repeated.json()["state"]["notifications"]), 1)
            self.assertEqual(client.post("/api/demo/action", json={"type": "reset"},
                                        headers={"Origin": "https://unrelated.example"}).status_code, 403)
            self.assertEqual(client.post("/api/demo/action", json={"type": "oops"}).status_code, 422)
            spot = client.post("/api/demo/point", json={"lat": 38.6, "lon": -121.9})
            self.assertEqual(spot.status_code, 200)
            self.assertEqual(set(spot.json()["layers"]), {"raw", "filtered"})
            self.assertEqual(client.post("/api/demo/point", json={"lat": 38.6}).status_code, 422)
            self.assertEqual(client.post("/api/demo/point", json={"lat": 50, "lon": -121.9}).status_code, 422)
            self.assertEqual(client.get("/api/demo/missing").status_code, 404)
            self.assertEqual(client.get("/api/demo/messages/unknown/details").status_code, 404)


if __name__ == "__main__":
    unittest.main()
