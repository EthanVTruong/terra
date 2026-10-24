"""Replay immutable held-out weather; record real QC, estimates and grower decisions.

Only this package's private session and demo orchard namespace are written. There
is deliberately no source, SMS dispatcher, optional LLM or transport configured.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
from threading import RLock
import uuid

import pandas as pd

from chillwatch.estimate import estimate_location
from terra_qc.reference import REFERENCE_ID, REFERENCE_SHA256, load_reference
from terra_monitoring.assessment import (
    _clean, _json, _observations, _operation_gate, _score, _stations,
    assess_location,
)
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store, fingerprint, utc
from terra_monitoring.farm_ops.writer import Writer
from terra_monitoring.interpolation import load_policy as load_interpolation_policy
from terra_monitoring.interpolation import spread as uncertainty_spread
from terra_monitoring.weather import VARIABLES, _physical_reason

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "docs/terra/demo-simulation/data"
START = "2026-03-11T11:00:00+00:00"
END = "2026-03-12T04:00:00+00:00"
WARMUP = "2026-03-10T11:00:00+00:00"
ORCHARD = {"lat": 38.55, "lon": -121.78, "elevation_ft": 60.0}
SESSION_KEY = "demo-simulation:session-v1"
PRESETS = ("normal", "minor", "spike", "support_loss", "recover")
MAP_BOUNDS = {"south": 38.22, "north": 38.86, "west": -122.45, "east": -121.14}
# Test sensors are uncalibrated inputs: the estimator may use them, frozen station QC never scores them.
SENSOR_BASE_ID = 900_000
MAX_SENSORS = 6


class DemoSimulation:
    """One shared local session, synchronized by two browser apps and durable SQLite."""

    def __init__(self, *, state_dir=None, data_dir=None, grid_size=8):
        self._lock = RLock()
        self.data_dir = Path(data_dir or DATA).resolve()
        self.provenance = json.loads((self.data_dir / "provenance.json").read_text(encoding="utf-8"))
        raw = (self.data_dir / self.provenance["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.provenance["sha256"]:
            raise ValueError("The immutable demonstration weather digest does not match")
        self._originals = json.loads(raw)
        weather_manifest = self.data_dir / "weather-provenance.json"
        self.weather_provenance = None
        self._weather_by_key = {}
        if weather_manifest.exists():
            self.weather_provenance = json.loads(weather_manifest.read_text(encoding="utf-8"))
            weather_raw = (self.data_dir / self.weather_provenance["file"]).read_bytes()
            if hashlib.sha256(weather_raw).hexdigest() != self.weather_provenance["sha256"]:
                raise ValueError("The immutable demonstration weather-variable digest does not match")
            self._weather_by_key = {(r["timestamp"], int(r["station_id"])): r for r in json.loads(weather_raw)}
        self.reference = load_reference()
        manifest = self.reference.manifest
        training_end, calibration_end = utc(manifest["train_end_exclusive"]), utc(manifest["validation_end_exclusive"])
        if any(utc(r["timestamp"]) < max(training_end, calibration_end) for r in self._originals):
            raise ValueError("Demonstration weather overlaps fitting or calibration")
        if self.provenance["reference_sha256"] != REFERENCE_SHA256:
            raise ValueError("The demonstration reference provenance does not match")
        geometry = manifest["station_geometry"]["stations"]
        self.station_metadata = [
            {"id": int(s), "station_id": int(s), "name": item["name"],
             "lat": item["latitude"], "lon": item["longitude"], "elevation_ft": item["elevation_ft"]}
            for s, item in sorted(geometry.items(), key=lambda pair: int(pair[0]))
        ]
        self.station_ids = {s["id"] for s in self.station_metadata}
        self.stations = _stations(pd.DataFrame(self.station_metadata))
        self._original_by_key = {(r["timestamp"], int(r["station_id"])): r for r in self._originals}
        self._grid_size = grid_size
        if isinstance(grid_size, bool) or not isinstance(grid_size, int) or not 2 <= grid_size <= 14:
            raise ValueError("Grid size must be between two and fourteen")
        directory = Path(state_dir or ROOT / ".terra-local/demo-simulation").resolve()
        directory.mkdir(parents=True, exist_ok=True)
        # An explicit path bypasses the live farm database environment variable.
        self.store = Store(directory / "demo.sqlite3")
        self.session = self.store.get(SESSION_KEY)
        self._state_cache = None
        self._map_cache = {}
        self.monitor = Monitor(self.store, writer=Writer(key="", model=""),
                               clock=lambda: utc(self.session["clock"]), delivery=None)
        if self.session is None:
            self._reset()
        else:
            if self.session.get("source_sha256") != self.provenance["sha256"]:
                raise ValueError("An existing demo session uses different source weather")
            if self.session.get("weather_source_sha256") != (self.weather_provenance or {}).get("sha256"):
                raise ValueError("This session predates the weather-variable source; choose a fresh simulation state directory")
            self.session.setdefault("sensors", [])
            self._evaluate()

    def _save(self):
        with self.store.connect(write=True) as db:
            self.store.set_state(db, SESSION_KEY, self.session)

    def _reset(self):
        run = uuid.uuid4().hex[:16]
        self.session = {"run_id": run, "orchard_id": "sim-" + run, "clock": START,
                        "source_sha256": self.provenance["sha256"], "revision": 0,
                        "weather_source_sha256": (self.weather_provenance or {}).get("sha256"),
                        "frames": {}, "offline": [], "sensors": [], "actions": [], "evaluations": {}, "weather_overrides": {}}
        self.monitor.create_orchard("Demo West Orchard", orchard_id=self.session["orchard_id"],
                                    mode="injected", **ORCHARD)
        self._evaluate()
        self._save()

    def _orchard(self):
        """The grower's pin: the starting location until moved with an orchard action."""
        return self.session.get("orchard", ORCHARD)

    def _move_orchard(self, lat, lon):
        # A moved pin is a new watched orchard, so alerts start fresh; earlier notes stay in the journal.
        geometry = {"lat": lat, "lon": lon, "elevation_ft": ORCHARD["elevation_ft"]}
        orchard_id = f"sim-{self.session['run_id']}-{self.session['revision'] + 1}"
        self.monitor.create_orchard("Demo West Orchard", orchard_id=orchard_id, mode="injected", **geometry)
        self.session.update(orchard_id=orchard_id, orchard=geometry, evaluations={})

    def _frame(self):
        return self.session["frames"].setdefault(self.session["clock"],
                                               {"temperatures": {}, "offline": list(self.session["offline"]),
                                                "weather": deepcopy(self.session.get("weather_overrides", {}))})

    def _inputs(self):
        now = utc(self.session["clock"])
        rows = []
        for original in self._originals:
            stamp = utc(original["timestamp"])
            if not utc(WARMUP) <= stamp <= now:
                continue
            row = deepcopy(original)
            extra = self._weather_by_key.get((original["timestamp"], int(original["station_id"])), {})
            row.update({key: value for key, value in extra.items() if key not in {"station_id", "timestamp"}})
            changes = self.session["frames"].get(original["timestamp"], {})
            station = row["station_id"]
            value = changes.get("temperatures", {}).get(str(station))
            for field, override in changes.get("weather", {}).get(str(station), {}).items():
                unit = {"relative_humidity_pct": "%", "wind_speed_mph": "mph", "solar_radiation_w_m2": "W/m2"}[field]
                row.update({field: override["value"], field + "_unit": unit, field + "_source_unit": unit,
                            field + "_source_value": override["value"], field + "_source_flag": override["source_flag"],
                            field + "_quality": "missing" if override["value"] is None else "flagged" if override["source_flag"] else "usable",
                            field + "_availability_reason": "missing_value" if override["value"] is None else "source_warning" if override["source_flag"] else "available"})
                reason = row[field + "_availability_reason"]
                row[field + "_available"] = reason == "available"
                row[field + "_availability_reasons"] = [] if reason == "available" else [reason]
                row.setdefault("demo_weather_overrides", {})[field] = deepcopy(override)
            if value is not None:
                row["temperature_f"] = value
                row["demo_override"] = {"kind": "temperature", "original_f": original["temperature_f"]}
            if station in changes.get("offline", []):
                row.update(temperature_f=None, quality="missing", availability_reason="simulated_offline")
                row["demo_override"] = {"kind": "offline", "original_f": original["temperature_f"]}
                for field in VARIABLES:
                    if field in row:
                        row.update({field: None, field + "_quality": "missing",
                                    field + "_availability_reason": "simulated_offline", field + "_available": False,
                                    field + "_availability_reasons": ["simulated_offline"]})
            rows.append(row)
        # A test sensor reports its set value every source hour in the replay window.
        hours = sorted({r["timestamp"] for r in rows})
        for sensor in self.session["sensors"]:
            # Set humidity/wind/solar readings use the same canonical fields as station weather,
            # so the existing peer-discrepancy policy judges them; unset variables are simply absent.
            weather = {}
            for field, value in sensor.get("weather", {}).items():
                unit = {"relative_humidity_pct": "%", "wind_speed_mph": "mph", "solar_radiation_w_m2": "W/m2"}[field]
                weather.update({field: value, field + "_unit": unit, field + "_source_unit": unit, field + "_source_value": value,
                                field + "_source_flag": "", field + "_quality": "usable", field + "_available": True,
                                field + "_availability_reason": "available", field + "_availability_reasons": []})
            rows.extend({"station_id": sensor["id"], "timestamp": hour, "temperature_f": sensor["value_f"],
                         "temperature_unit": "F", "quality": "usable", "source_flag": "", "timestamp_verified": True,
                         "timestamp_basis": "fixed_pst_hour_ending", "availability_reason": None,
                         "source": "demo_test_sensor", "point_sensor": True, "demo_override": {"kind": "test_sensor"},
                         **weather} for hour in hours)
        return pd.DataFrame(rows)

    def _sensor_limits(self):
        """Temperature uncertainty measured from the replay: each calibrated station predicted by
        Nathan's estimator from the others over recent hours, test sensors left out."""
        policy, now = load_interpolation_policy(), utc(self.session["clock"])
        hours = [h for h in (now - timedelta(hours=i) for i in range(policy["uncertainty_history_hours"])) if h >= utc(WARMUP)]
        usable = self.cleaned[self.cleaned.operation_available & self.cleaned.available
                              & self.cleaned.station_id.isin(self.station_ids)]
        observed = {(int(r.station_id), pd.Timestamp(r.timestamp)): r.temperature_f for r in usable.itertuples()}
        sensors = tuple(s["id"] for s in self.session["sensors"])
        residuals = []
        for metadata in self.station_metadata:
            frame = estimate_location(metadata["lat"], metadata["lon"], metadata["elevation_ft"], hours, now,
                self.cleaned[self.cleaned.operation_available], self.stations, None,
                location_id="demo-loo", geometry_source="leave-one-out at a calibrated station",
                exclude_station_ids=(metadata["id"], *sensors), max_age_hours=len(hours) + 1)
            for row in frame.itertuples():
                truth = observed.get((metadata["id"], pd.Timestamp(row.valid_utc)))
                if truth is not None and row.status in {"ok", "stale"} and math.isfinite(row.temperature):
                    residuals.append(truth - row.temperature)
        width = uncertainty_spread("temperature_f", residuals, policy, hours=len(hours))
        return {"uncertainty_f": width, "band_f": policy["band_multiplier"] * width,
                "extreme_f": policy["extreme_multiplier"] * width, "sustained_readings": policy["sustained_readings"]}

    def _sensor_checks(self):
        """Compare each test sensor with Nathan's estimate from the calibrated stations (all sensors held out),
        outside the measured uncertainty: beyond the extreme band once, or the band for consecutive readings."""
        if not self.session["sensors"]:
            return {}
        limits, now = self._sensor_limits(), utc(self.session["clock"])
        hours = [now - timedelta(hours=i) for i in reversed(range(limits["sustained_readings"]))]
        hours = [h for h in hours if h >= utc(WARMUP)]
        findings = self._layer_findings()[1][1]
        sensors = tuple(s["id"] for s in self.session["sensors"])
        checks = {}
        for sensor in self.session["sensors"]:
            frame = estimate_location(sensor["lat"], sensor["lon"], ORCHARD["elevation_ft"], hours, now,
                self.cleaned[self.cleaned.operation_available], self.stations, findings,
                location_id="demo-sensor", geometry_source="test sensor location; configured 60 ft elevation",
                exclude_station_ids=sensors, max_age_hours=6)
            residuals = [sensor["value_f"] - row.temperature if row.status == "ok" else None for row in frame.itertuples()]
            current = residuals[-1]
            outside = [r is not None and abs(r) > limits["band_f"] for r in residuals]
            level = ("none" if current is None else "strong" if abs(current) > limits["extreme_f"]
                     else "moderate" if len(outside) == limits["sustained_readings"] and all(outside) else "none")
            checks[sensor["id"]] = {"observed_f": sensor["value_f"], "level": level, **limits,
                                    "expected_f": None if current is None else sensor["value_f"] - current,
                                    "deviation_f": current}
        return checks

    def _all_stations(self):
        sensors = [{"id": s["id"], "station_id": s["id"], "name": "Test sensor", "lat": s["lat"], "lon": s["lon"],
                    "elevation_ft": ORCHARD["elevation_ft"]} for s in self.session["sensors"]]
        return _stations(pd.DataFrame(self.station_metadata + sensors))

    def _evaluate(self):
        observations = self._inputs()
        self.stations = self._all_stations()
        now = pd.Timestamp(self.session["clock"])
        snapshot = fingerprint({"source": self.provenance["sha256"], "weather_source": self.weather_provenance,
                                "frames": self.session["frames"], "sensors": self.session["sensors"],
                                "clock": self.session["clock"]})
        self.packet = assess_location(**self._orchard(), valid_utc=now, as_of=now,
            observations=observations, stations=self.stations, snapshot_id="demo:" + snapshot,
            data_mode="injected", location_id=self.session["orchard_id"], reference=self.reference,
            geometry_source="explicit demonstration orchard pin; 60 ft configured elevation")
        self.observations = observations
        self.cleaned = _operation_gate(_clean(_observations(observations, now), self.station_ids), self.stations)
        known = self.cleaned[self.cleaned.operation_available & self.cleaned.station_id.isin(self.station_ids)].copy()
        known["quality"] = known["source_quality"]
        self.scored = _score(known, self.reference)
        self.sensor_checks = self._sensor_checks()
        flags = [{"sensor_id": sid, "level": c["level"], "observed_f": c["observed_f"], "expected_f": c["expected_f"],
                  "deviation_f": c["deviation_f"], "valid_utc": self.session["clock"]}
                 for sid, c in self.sensor_checks.items() if c["level"] != "none"]
        self.monitor.apply(self.session["orchard_id"], {"latest": self.packet, "sensor_flags": flags}, now=now)
        published = self.monitor.flush(self.session["orchard_id"], now=now)
        self.session["evaluations"][self.session["clock"]] = {
            "orchard_f": self.packet["location_estimate"]["temperature"],
            "orchard_available": self.packet["location_estimate"]["status"] == "ok",
        }
        if published is not None:
            self._retain_note_snapshot(published)
        self._state_cache = None

    def _advance(self, hours):
        if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 6:
            raise ValueError("Advance between one and six distinct observation hours")
        if utc(self.session["clock"]) + timedelta(hours=hours) > utc(END):
            raise ValueError("This held-out demonstration has reached its last hour; reset to begin again")
        for _ in range(hours):
            self.session["clock"] = (utc(self.session["clock"]) + timedelta(hours=1)).isoformat()
            self._frame()
            self._evaluate()

    def _station_id(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value not in self.station_ids:
            raise ValueError("Choose one of the five calibrated stations")
        return value

    def _sensor(self, value):
        match = [s for s in self.session["sensors"] if not isinstance(value, bool) and s["id"] == value]
        if not match:
            raise ValueError("Choose an existing test sensor")
        return match[0]

    @staticmethod
    def _temperature(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -10 <= value <= 125:
            raise ValueError("Temperature must be finite and between -10 and 125 degrees Fahrenheit")
        return float(value)

    @staticmethod
    def _spot(lat, lon):
        for value in (lat, lon):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Latitude and longitude must be finite numbers")
        if not (MAP_BOUNDS["south"] <= lat <= MAP_BOUNDS["north"] and MAP_BOUNDS["west"] <= lon <= MAP_BOUNDS["east"]):
            raise ValueError("Choose a spot inside the demonstration map")
        return float(lat), float(lon)

    def action(self, action):
        with self._lock:
            if not isinstance(action, dict):
                raise ValueError("Provide a demonstration action")
            kind = action.get("type")
            fields = {"temperature": {"type", "station_id", "value_f"},
                      "weather": {"type", "station_id", "variable", "value", "source_flag"},
                      "weather_reset": {"type", "station_id", "variable"},
                      "online": {"type", "station_id", "online"}, "advance": {"type", "hours"},
                      "preset": {"type", "name"}, "reset": {"type"}, "reset_temperatures": {"type"},
                      "sensor": {"type", "lat", "lon", "value_f"}, "sensor_value": {"type", "sensor_id", "value_f"},
                      "sensor_remove": {"type", "sensor_id"}, "orchard": {"type", "lat", "lon"},
                      "sensor_weather": {"type", "sensor_id", "variable", "value"}}
            if kind not in fields or set(action) != fields[kind]:
                raise ValueError("Unknown action or unexpected action fields")
            if kind == "reset":
                self._reset()
            elif kind == "reset_temperatures":
                # Recorded readings return for this hour and test sensors go; reporting interruptions stay.
                self._frame()["temperatures"] = {}
                self.session["sensors"] = []
                self._evaluate()
            elif kind == "temperature":
                station, value = self._station_id(action["station_id"]), self._temperature(action["value_f"])
                self._frame()["temperatures"][str(station)] = value
                self._evaluate()
            elif kind == "sensor":
                lat, lon = self._spot(action["lat"], action["lon"])
                value = self._temperature(action["value_f"])
                if len(self.session["sensors"]) >= MAX_SENSORS:
                    raise ValueError(f"Remove a test sensor first; at most {MAX_SENSORS} can be placed")
                self.session["sensors"].append({"id": SENSOR_BASE_ID + self.session["revision"] + 1,
                                                "lat": lat, "lon": lon, "value_f": value})
                self._evaluate()
            elif kind == "sensor_value":
                sensor, value = self._sensor(action["sensor_id"]), self._temperature(action["value_f"])
                sensor["value_f"] = value
                self._evaluate()
            elif kind == "sensor_remove":
                sensor = self._sensor(action["sensor_id"])
                self.session["sensors"] = [s for s in self.session["sensors"] if s is not sensor]
                self._evaluate()
            elif kind == "sensor_weather":
                sensor, variable, value = self._sensor(action["sensor_id"]), action["variable"], action["value"]
                if variable not in VARIABLES:
                    raise ValueError("Choose humidity, wind speed or solar radiation")
                if value is None:
                    sensor.setdefault("weather", {}).pop(variable, None)
                else:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                            or _physical_reason(variable, float(value)):
                        raise ValueError("That reading is outside the physical range for this variable")
                    sensor.setdefault("weather", {})[variable] = float(value)
                self._evaluate()
            elif kind == "orchard":
                self._move_orchard(*self._spot(action["lat"], action["lon"]))
                self._evaluate()
            elif kind == "online":
                station, online = self._station_id(action["station_id"]), action["online"]
                if not isinstance(online, bool):
                    raise ValueError("Online must be a boolean")
                offline = set(self.session["offline"])
                offline.discard(station) if online else offline.add(station)
                self.session["offline"] = sorted(offline)
                self._frame()["offline"] = sorted(offline)
                self._evaluate()
            elif kind in {"weather", "weather_reset"}:
                station, field = self._station_id(action["station_id"]), action["variable"]
                if field not in VARIABLES:
                    raise ValueError("Choose humidity, average wind speed or solar radiation")
                overrides = self.session.setdefault("weather_overrides", {}).setdefault(str(station), {})
                if kind == "weather_reset":
                    overrides.pop(field, None)
                else:
                    value, flag = action["value"], action["source_flag"]
                    limit = {"relative_humidity_pct": 100, "wind_speed_mph": 150, "solar_radiation_w_m2": 2000}[field]
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                             or not math.isfinite(value) or not 0 <= value <= limit):
                        raise ValueError("Use a finite value in the displayed range, or null for missing")
                    if flag not in {"", "Y", "M", "P", "I", "R", "S", "N"}:
                        raise ValueError("Choose a supported source quality flag")
                    overrides[field] = {"value": value, "source_flag": flag}
                self._frame()["weather"] = deepcopy(self.session["weather_overrides"])
                self._evaluate()
            elif kind == "advance":
                self._advance(action["hours"])
            else:
                name = action["name"]
                if name not in PRESETS:
                    raise ValueError("Choose an available demonstration preset")
                if name == "recover":
                    # Never convert three same-hour reevaluations into recovery.
                    if utc(self.session["clock"]) + timedelta(hours=3) > utc(END):
                        raise ValueError("Recovery needs three remaining real hourly snapshots; reset first")
                    self.session["offline"] = []
                    self._advance(3)
                else:
                    self.session["offline"] = [139, 212, 235] if name == "support_loss" else []
                    frame = self._frame()
                    self.session["weather_overrides"] = {}
                    frame.update(temperatures={}, offline=list(self.session["offline"]), weather={})
                    if name in {"minor", "spike"}:
                        source = self._original_by_key[(self.session["clock"], 6)]
                        frame["temperatures"]["6"] = source["temperature_f"] + (1.0 if name == "minor" else 24.0)
                    self._evaluate()
            self.session["revision"] += 1
            self.session["actions"] = (self.session["actions"] + [
                {"sequence": self.session["revision"], "timestamp": self.session["clock"], **deepcopy(action)}])[-200:]
            self._save()
            return self.state()

    def question(self, text, request_id=None):
        with self._lock:
            identity = request_id if request_id is not None else uuid.uuid4().hex
            existing = self.store.message(fingerprint({"orchard": self.session["orchard_id"],
                                                       "events": [], "question": identity}))
            message = self.monitor.ask(self.session["orchard_id"], text, request_id=identity)
            if existing is None:
                self._retain_note_snapshot(message)
            self.session["revision"] += 1
            self._state_cache = None
            self._save()
            return {"message": message, "state": self.state()}

    def _layer_findings(self):
        current = self.session["clock"]
        exclusions = pd.DataFrame([
            {"station_id": row["station_id"], "timestamp": current, "reason": row["reason"]}
            for row in self.packet["estimator_qc_exclusions"]
        ], columns=["station_id", "timestamp", "reason"])
        return (("raw", None), ("filtered", exclusions))

    def _estimate(self, lat, lon, findings, location_id, geometry_source):
        # Nathan's public function, with identical geometry and source eligibility
        # for every map point. The only layer difference is real QC findings.
        current = self.session["clock"]
        return estimate_location(lat, lon, ORCHARD["elevation_ft"], [current], current,
            self.cleaned[self.cleaned.operation_available], self.stations, findings,
            location_id=location_id, geometry_source=geometry_source, max_age_hours=6).iloc[0].to_dict()

    def point(self, lat, lon):
        """Read-only estimate at one map spot; it never changes the session."""
        lat, lon = self._spot(lat, lon)
        with self._lock:
            layers = {}
            for layer, findings in self._layer_findings():
                estimate = self._estimate(lat, lon, findings, "demo-spot",
                                          "selected demonstration map spot; configured 60 ft elevation")
                layers[layer] = _json({"temperature_f": estimate["temperature"], "status": estimate["status"],
                                       "n_used": estimate["n_used"], "used_station_ids": estimate["used_station_ids"]})
            return {"lat": lat, "lon": lon, "clock": self.session["clock"], "elevation_ft": ORCHARD["elevation_ft"],
                    "layers": layers}

    def _heatmap(self):
        current = self.session["clock"]
        key = fingerprint({"current": current, "frames": self.session["frames"], "sensors": self.session["sensors"],
                           "grid": self._grid_size})
        if key in self._map_cache:
            return self._map_cache[key]
        bounds = MAP_BOUNDS
        size = self._grid_size
        layers = {"raw": [], "filtered": []}
        for row in range(size):
            lat = bounds["south"] + (bounds["north"] - bounds["south"]) * row / (size - 1)
            for column in range(size):
                lon = bounds["west"] + (bounds["east"] - bounds["west"]) * column / (size - 1)
                for layer, findings in self._layer_findings():
                    estimate = self._estimate(lat, lon, findings, "demo-grid",
                                              "regular demonstration grid; configured 60 ft elevation")
                    layers[layer].append(_json({"lat": lat, "lon": lon, "row": row, "column": column,
                        "temperature_f": estimate["temperature"], "status": estimate["status"],
                        "n_used": estimate["n_used"], "used_station_ids": estimate["used_station_ids"]}))
        result = {"bounds": bounds, "rows": size, "columns": size, "layers": layers,
                  "cells": layers["filtered"], "method_version": self.packet["location_estimate"]["method_version"],
                  "elevation_ft": ORCHARD["elevation_ft"], "fingerprint": key,
                  "basis": "Nathan estimator at each grid point; fixed 60 ft demonstration elevation",
                  "raw_label": "Source eligible, before station QC", "filtered_label": "After actual Terra QC"}
        # Bound memory while preserving the current map across one-second polls.
        if len(self._map_cache) >= 16:
            self._map_cache.pop(next(iter(self._map_cache)))
        self._map_cache[key] = result
        return result

    @staticmethod
    def _assessment(row, online):
        return _json({"available": bool(online and row.get("reference_status") == "available"),
            "source_available": bool(online and row.get("available", False)),
            "expected_f": row.get("expected_f"), "residual_f": row.get("residual"),
            "level": row.get("level", "none"),
            "reason": row.get("status", "missing") if online else "simulated_offline",
            "required_readings": row.get("required_readings"), "typical_low_f": row.get("typical_low_f"),
            "typical_high_f": row.get("typical_high_f"), "neighbor_count": row.get("neighbor_count", 0),
            "extreme_low_f": row.get("extreme_low_f"), "extreme_high_f": row.get("extreme_high_f")})

    def _station_view(self):
        current = self.session["clock"]
        changes = self.session["frames"].get(current, {})
        current_scores = self.scored[self.scored.timestamp.eq(pd.Timestamp(current))]
        scored_by_station = {int(r["station_id"]): r for r in current_scores.to_dict("records")}
        source_by_station = {int(r["station_id"]): r for r in self.observations[
            self.observations.timestamp.eq(current)].to_dict("records")}
        # Input timestamps are ISO strings; the scorer owns normalized dates.
        stations = []
        flagged = {f["station_id"] for f in self.packet["station_findings"]}
        for metadata in self.station_metadata:
            sid = metadata["id"]
            original = self._original_by_key.get((current, sid), {})
            online = sid not in changes.get("offline", [])
            source = source_by_station.get(sid, {})
            stations.append({**metadata, "original_f": original.get("temperature_f"),
                "value_f": source.get("temperature_f") if online else None,
                "online": online, "override": changes.get("temperatures", {}).get(str(sid)),
                "source_quality": original.get("quality"), "source_flag": original.get("source_flag"),
                "flagged": sid in flagged, "assessment": self._assessment(scored_by_station.get(sid, {}), online),
                "weather": {variable["variable"]: next((r for r in variable["stations"] if r["station_id"] == sid), None)
                            for variable in self.packet.get("weather", {}).get("variables", [])}})
        return _json(stations)

    def _history_view(self):
        current = self.session["clock"]
        history = []
        for row in self.scored.to_dict("records"):
            stamp = pd.Timestamp(row["timestamp"]).isoformat()
            if pd.Timestamp(stamp) < pd.Timestamp(current) - pd.Timedelta(hours=24):
                continue
            sid = int(row["station_id"])
            online = sid not in self.session["frames"].get(stamp, {}).get("offline", [])
            history.append(_json({"timestamp": stamp, "station_id": sid,
                "original_f": self._original_by_key.get((stamp, sid), {}).get("temperature_f"),
                "value_f": row.get("temperature_f") if online else None,
                "expected_f": row.get("expected_f"), "level": row.get("level", "none"),
                "online": online, **self.session["evaluations"].get(stamp,
                    {"orchard_f": None, "orchard_available": False})}))
        return history

    def _snapshot_key(self, message_id):
        return "demo-simulation:snapshot:" + self.session["run_id"] + ":" + message_id

    def _retain_note_snapshot(self, message):
        """Keep first-publication demo context without changing farm evidence.

        Reading an old note or resuming a session never reconstructs its context
        from today's mutable controls. This intentionally excludes the heatmap.
        """
        key = self._snapshot_key(message["id"])
        with self.store.connect(write=True) as db:
            if self.store.state(db, key) is None:
                self.store.set_state(db, key, {"schema_version": "terra-demo-note-snapshot-v1",
                    "message_id": message["id"], "run_id": self.session["run_id"],
                    "timestamp": self.session["clock"], "stations": self._station_view(),
                    "history": self._history_view(), "assessment": self.packet})

    def state(self):
        with self._lock:
            if self._state_cache is not None:
                return deepcopy(self._state_cache)
            current = self.session["clock"]
            stations, history = self._station_view(), self._history_view()
            orchard_id = self.session["orchard_id"]
            watch = self.store.get("watch:" + orchard_id, {})
            monitor = self.monitor.status(orchard_id)
            monitor["demo_health"] = {"incident_active": bool(watch.get("health_incident")),
                "consecutive_good_readings": watch.get("health_good_count", 0), "required_good_readings": 3}
            events = self.store.events(orchard_id)
            types = {e["id"]: e["type"] for e in events}
            messages = [{**m, "event_types": [types[e] for e in m["event_ids"]],
                         "delivery": {"status": "simulation_only", "channel": "simulated_phone"}}
                        for m in reversed(self.store.messages(orchard_id))]
            notifications = [{"id": m["id"], "message_id": m["id"], "text": m["text"],
                "created_at": m["created_at"], "event_types": m["event_types"],
                "channel": "simulated_phone", "status": "simulated", "provider_delivery": False}
                for m in messages if m["kind"] == "proactive" and any(t != "welcome" for t in m["event_types"])]
            self._state_cache = _json({"schema_version": "terra-demo-simulation-v1", "run_id": self.session["run_id"],
                "revision": self.session["revision"], "data_mode": "injected", "clock": {"current_utc": current,
                    "start_utc": START, "end_utc": END, "warmup_start_utc": WARMUP,
                    "label": "Historical observation hour; advances only when controlled"},
                "source": {**self.provenance, "no_training_overlap": True, "immutable": True,
                           "weather_variables": self.weather_provenance},
                "stations": stations, "assessment": self.packet, "heatmap": self._heatmap(),
                "sensors": [{**s, "elevation_ft": ORCHARD["elevation_ft"], "calibrated": False,
                             "check": self.sensor_checks.get(s["id"]),
                             "weather_checks": {variable["variable"]: next((r for r in variable["stations"] if r["station_id"] == s["id"]), None)
                                                for variable in self.packet.get("weather", {}).get("variables", [])}}
                            for s in self.session["sensors"]],
                "orchard": self.store.orchard(orchard_id), "monitor": monitor,
                "messages": messages, "notifications": notifications, "events": events, "history": history,
                "actions": self.session["actions"], "controls": {"presets": list(PRESETS),
                    "temperature_min_f": -10, "temperature_max_f": 125, "advance_max_hours": 6,
                    "remaining_hours": int((utc(END) - utc(current)).total_seconds() / 3600)},
                "side_effects": {"real_sms": False, "provider_calls": False, "llm_calls": False,
                    "model_training": False, "live_state": False},
                "note": "Real held-out weather with explicit local overrides. Phone notifications are simulated copies of persisted grower notes."})
            return deepcopy(self._state_cache)

    def details(self, message_id):
        with self._lock:
            result = self.store.details(self.session["orchard_id"], message_id)
            result["demo_snapshot"] = self.store.get(self._snapshot_key(message_id))
            return result
