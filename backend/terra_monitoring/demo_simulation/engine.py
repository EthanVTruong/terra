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

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "docs/terra/demo-simulation/data"
START = "2026-03-11T11:00:00+00:00"
END = "2026-03-12T04:00:00+00:00"
WARMUP = "2026-03-10T11:00:00+00:00"
ORCHARD = {"lat": 38.55, "lon": -121.78, "elevation_ft": 60.0}
SESSION_KEY = "demo-simulation:session-v1"
PRESETS = ("normal", "minor", "spike", "support_loss", "recover")
MAP_BOUNDS = {"south": 38.22, "north": 38.86, "west": -122.45, "east": -121.14}


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
            self._evaluate()

    def _save(self):
        with self.store.connect(write=True) as db:
            self.store.set_state(db, SESSION_KEY, self.session)

    def _reset(self):
        run = uuid.uuid4().hex[:16]
        self.session = {"run_id": run, "orchard_id": "sim-" + run, "clock": START,
                        "source_sha256": self.provenance["sha256"], "revision": 0,
                        "frames": {}, "offline": [], "actions": [], "evaluations": {}}
        self.monitor.create_orchard("Demo West Orchard", orchard_id=self.session["orchard_id"],
                                    mode="injected", **ORCHARD)
        self._evaluate()
        self._save()

    def _frame(self):
        return self.session["frames"].setdefault(self.session["clock"],
                                               {"temperatures": {}, "offline": list(self.session["offline"])})

    def _inputs(self):
        now = utc(self.session["clock"])
        rows = []
        for original in self._originals:
            stamp = utc(original["timestamp"])
            if not utc(WARMUP) <= stamp <= now:
                continue
            row = deepcopy(original)
            changes = self.session["frames"].get(original["timestamp"], {})
            station = row["station_id"]
            value = changes.get("temperatures", {}).get(str(station))
            if value is not None:
                row["temperature_f"] = value
                row["demo_override"] = {"kind": "temperature", "original_f": original["temperature_f"]}
            if station in changes.get("offline", []):
                row.update(temperature_f=None, quality="missing", availability_reason="simulated_offline")
                row["demo_override"] = {"kind": "offline", "original_f": original["temperature_f"]}
            rows.append(row)
        return pd.DataFrame(rows)

    def _evaluate(self):
        observations = self._inputs()
        now = pd.Timestamp(self.session["clock"])
        snapshot = fingerprint({"source": self.provenance["sha256"], "frames": self.session["frames"],
                                "clock": self.session["clock"]})
        self.packet = assess_location(**ORCHARD, valid_utc=now, as_of=now,
            observations=observations, stations=self.stations, snapshot_id="demo:" + snapshot,
            data_mode="injected", location_id=self.session["orchard_id"], reference=self.reference,
            geometry_source="explicit demonstration orchard pin; 60 ft configured elevation")
        self.observations = observations
        self.cleaned = _operation_gate(_clean(_observations(observations, now), self.station_ids), self.stations)
        known = self.cleaned[self.cleaned.operation_available].copy()
        known["quality"] = known["source_quality"]
        self.scored = _score(known, self.reference)
        self.monitor.apply(self.session["orchard_id"], {"latest": self.packet}, now=now)
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

    def action(self, action):
        with self._lock:
            if not isinstance(action, dict):
                raise ValueError("Provide a demonstration action")
            kind = action.get("type")
            fields = {"temperature": {"type", "station_id", "value_f"},
                      "online": {"type", "station_id", "online"}, "advance": {"type", "hours"},
                      "preset": {"type", "name"}, "reset": {"type"}, "reset_temperatures": {"type"}}
            if kind not in fields or set(action) != fields[kind]:
                raise ValueError("Unknown action or unexpected action fields")
            if kind == "reset":
                self._reset()
            elif kind == "reset_temperatures":
                # Recorded readings return for this hour; reporting interruptions stay.
                self._frame()["temperatures"] = {}
                self._evaluate()
            elif kind == "temperature":
                station, value = self._station_id(action["station_id"]), action["value_f"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -10 <= value <= 125:
                    raise ValueError("Temperature must be finite and between -10 and 125 degrees Fahrenheit")
                self._frame()["temperatures"][str(station)] = float(value)
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
                    frame.update(temperatures={}, offline=list(self.session["offline"]))
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
        for value in (lat, lon):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Latitude and longitude must be finite numbers")
        if not (MAP_BOUNDS["south"] <= lat <= MAP_BOUNDS["north"] and MAP_BOUNDS["west"] <= lon <= MAP_BOUNDS["east"]):
            raise ValueError("Choose a spot inside the demonstration map")
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
        key = fingerprint({"current": current, "frames": self.session["frames"], "grid": self._grid_size})
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
                "flagged": sid in flagged, "assessment": self._assessment(scored_by_station.get(sid, {}), online)})
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
                "source": {**self.provenance, "no_training_overlap": True, "immutable": True},
                "stations": stations, "assessment": self.packet, "heatmap": self._heatmap(),
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
