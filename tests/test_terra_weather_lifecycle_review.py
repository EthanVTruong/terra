"""Lifecycle regression scenarios independent of the weather detector formulas."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from terra_monitoring.farm_ops.service import Monitor, _event, orchard_profile
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from terra_monitoring.weather_events import decide, notification_current
from terra_monitoring.live import normalize_cimis_payload
from terra_monitoring.weather import assess_weather


START = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)
HUMIDITY = "relative_humidity_pct"


def assessment(hour, *, fault=False, level=None, reason=None, variable=HUMIDITY):
    stamp = (START + timedelta(hours=hour)).isoformat()
    station = {"station_id": 6, "status": "unavailable" if reason else "assessable",
               "reason": reason or "peer_comparison", "level": level or ("strong" if fault else "none"),
               "observed": 95 if fault else 50, "expected": 50, "departure": 45 if fault else 0}
    if reason == "insufficient_daylight_context":
        station["status"] = "low_daylight"
    return {"query": {"valid_utc": stamp}, "weather": {
        "enabled": True, "policy_version": "terra-weather-policy-v1",
        "variables": [{"variable": variable, "label": "Relative humidity", "unit": "%", "stations": [station]}],
        "findings": [{"variable": variable, "station_id": 6, "severity": level or "strong", "start_utc": stamp}]
                    if fault else []}}


class WeatherLifecycleReviewTests(unittest.TestCase):
    def setUp(self):
        self.profile = orchard_profile(orchard_id="review", mode="current")
        self.state = {}

    def step(self, hour, *, now_hour=None, **kwargs):
        packet = assessment(hour, **kwargs)
        now = START + timedelta(hours=hour if now_hour is None else now_hour)
        result = decide(self.profile, self.state, packet, now, "evidence", _event)
        self.state["last_observation"] = packet["query"]["valid_utc"]
        return result

    def recovered(self):
        opening = self.step(0, fault=True)[0]
        self.step(1)
        self.step(2)
        recovery = self.step(3)[0]
        return opening, recovery

    def test_old_opening_cannot_return_during_a_new_incident(self):
        old, _ = self.recovered()
        new = self.step(4, fault=True)[0]
        self.assertNotEqual(old["episode_id"], new["episode_id"])
        self.assertFalse(notification_current(old, self.state))
        self.assertTrue(notification_current(new, self.state))

    def test_only_latest_recovery_can_be_delivered(self):
        _, old_recovery = self.recovered()
        self.step(4, fault=True)
        self.step(5)
        self.step(6)
        recovery = self.step(7)[0]
        self.assertFalse(notification_current(old_recovery, self.state))
        self.assertTrue(notification_current(recovery, self.state))

    def test_same_hour_source_warning_revokes_recovery(self):
        _, recovery = self.recovered()
        self.step(3, reason="source_warning")
        self.assertFalse(notification_current(recovery, self.state))

    def test_same_hour_repeat_cannot_supply_three_recovery_hours(self):
        self.step(0, fault=True)
        self.assertEqual(self.step(1), [])
        self.assertEqual(self.step(1), [])
        self.assertEqual(self.step(1), [])
        self.assertEqual(self.step(2), [])
        self.assertEqual([e["type"] for e in self.step(3)], ["weather_discrepancy_recovered"])

    def test_gaps_and_missing_data_do_not_prove_recovery(self):
        self.step(0, fault=True)
        self.step(1)
        self.step(2, reason="missing_observation")
        self.step(3)
        self.assertFalse(any(e["type"] == "weather_discrepancy_recovered" for e in self.step(5)))
        self.assertFalse(any(e["type"] == "weather_discrepancy_recovered" for e in self.step(6)))
        self.assertTrue(any(e["type"] == "weather_discrepancy_recovered" for e in self.step(7)))

    def test_source_warning_does_not_become_numerical_discrepancy(self):
        events = self.step(0, reason="source_warning")
        self.assertEqual([e["type"] for e in events], ["weather_data_limited"])

    def test_low_daylight_is_not_missing_solar_data(self):
        events = self.step(0, reason="insufficient_daylight_context", variable="solar_radiation_w_m2")
        self.assertEqual(events, [])
        self.assertFalse(self.state["weather_health_incidents"])

    def test_stale_fault_cannot_open_numerical_incident(self):
        events = self.step(0, fault=True, now_hour=7)
        self.assertEqual([e["type"] for e in events], ["weather_data_limited"])

    def test_escalation_supersedes_unsent_moderate_opening(self):
        opening = self.step(0, fault=True, level="moderate")[0]
        escalation = self.step(1, fault=True, level="strong")[0]
        self.assertEqual(opening["episode_id"], escalation["episode_id"])
        self.assertFalse(notification_current(opening, self.state))
        self.assertTrue(notification_current(escalation, self.state))

    def test_flush_keeps_recovery_and_consumes_obsolete_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "review.sqlite3")
            monitor = Monitor(store=store, writer=Writer(key="", model=""), clock=lambda: START + timedelta(hours=3))
            store.save_orchard(self.profile)
            pending = []
            for hour in range(4):
                pending.extend(self.step(hour, fault=(hour == 0)))
            with store.connect(write=True) as db:
                for event in pending:
                    store.add_event(db, self.profile["id"], event, event["occurred_at"])
                store.set_state(db, "watch:" + self.profile["id"], self.state)
            message = monitor.flush(self.profile["id"], now=START + timedelta(hours=3))
            self.assertEqual(message["event_ids"], [pending[-1]["id"]])
            self.assertIn("back within", message["text"])
            self.assertNotIn("cause unconfirmed", message["text"])
            self.assertEqual(store.pending(self.profile["id"]), [])
            self.assertIsNone(monitor.flush(self.profile["id"], now=START + timedelta(hours=3)))

    def test_native_conflicting_duplicate_preserves_variable_reason(self):
        records = []
        stations = []
        for sid in range(1, 5):
            records.append({"Station": str(sid), "Date": "2026-10-24", "Hour": "0100", "Scope": "hourly",
                "HlyAirTmp": {"Value": "40", "Unit": "(F)", "Qc": " "},
                "HlyRelHum": {"Value": "50", "Unit": "(%)", "Qc": " "},
                "HlyWindSpd": {"Value": "5", "Unit": "(mph)", "Qc": " "},
                "HlySolRad": {"Value": "500", "Unit": "(W/m2)", "Qc": " "}})
            stations.append({"station_id": sid, "lat": 38.5 + sid * .001, "lon": -121.8})
        records.append({**records[0], "HlyRelHum": {"Value": "70", "Unit": "(%)", "Qc": " "}})
        stamp = START + timedelta(hours=1)
        rows, _ = normalize_cimis_payload({"Data": {"Providers": [{"Records": records}]}},
            requested_station_ids=range(1, 5), observed_at=stamp)
        weather = assess_weather(rows, stations, valid_time_utc=stamp, as_of_utc=stamp)
        by_variable = {v["variable"]: v for v in weather["variables"]}
        humidity = next(s for s in by_variable[HUMIDITY]["stations"] if s["station_id"] == 1)
        wind = next(s for s in by_variable["wind_speed_mph"]["stations"] if s["station_id"] == 1)
        self.assertEqual(humidity["reason"], "conflicting_duplicate")
        self.assertEqual(wind["status"], "assessable")


if __name__ == "__main__":
    unittest.main()
