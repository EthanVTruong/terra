"""Reuse the current query service and its immutable snapshots for bounded catch-up."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

import pandas as pd

from terra_monitoring.assessment import assess_location


class LiveSource:
    def __init__(self, service=None):
        if service is None:
            from terra_monitoring.live import LiveQueryService
            from .store import DEFAULT_DB
            database = Path(os.environ.get("TERRA_FARM_DB", DEFAULT_DB)).expanduser().resolve()
            service = LiveQueryService(data_dir=database.parent / "farm-live-source")
        self.service = service
        self._signatures = {}
        self._history = {}

    def collect(self, profile):
        if profile["mode"] != "current":
            raise ValueError("A live source cannot populate a replay orchard")
        query = {k: profile[k] for k in ("lat", "lon", "elevation_ft")}
        query["location_id"] = profile["id"]
        latest = self.service.assess(query)
        provenance = latest["provenance"]
        identity = provenance.get("data_snapshot_id", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ValueError("The live assessment must reference its immutable source snapshot")
        path = Path(self.service.data_dir) / "snapshots" / (identity[7:] + ".json")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Unexpectedly large recent snapshot")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != identity[7:]:
            raise ValueError("Source snapshot failed integrity verification")
        snapshot = json.loads(raw)
        if snapshot["data_mode"] != "current":
            raise ValueError("Replay observations cannot enter the live monitor")
        signature = hashlib.sha256(json.dumps({k: snapshot[k] for k in ("observations", "stations")}, sort_keys=True).encode()).hexdigest()
        if self._signatures.get(profile["id"]) == signature:
            # Retrying a failed DB commit must not lose the catch-up hours.
            history = [p for p in self._history[profile["id"]] if p["query"]["valid_utc"] != latest["query"]["valid_utc"]]
            return {"latest": latest, "history": [*history, latest]}
        observations = pd.DataFrame(snapshot["observations"])
        stations = pd.DataFrame(snapshot["stations"])
        clock = pd.Timestamp(latest["query"]["as_of_utc"])
        latest_hour = pd.Timestamp(latest["query"]["valid_utc"])
        hours = sorted(set(pd.to_datetime(observations.timestamp, utc=True)))
        # The live query supplies at most three days. Never launch a seasonal backfill here.
        if len(hours) > 73:
            raise ValueError("Recent catch-up exceeded the three-day bound")
        history = []
        for valid in hours:
            if valid > clock:
                continue
            if valid == latest_hour:
                history.append(latest)
                continue
            history.append(assess_location(**{k: profile[k] for k in ("lat", "lon", "elevation_ft")},
                valid_utc=valid, as_of=clock, observations=observations, stations=stations,
                snapshot_id=identity, data_mode="current", location_id=profile["id"],
                reference=self.service.reference, max_age_hours=6,
                geometry_source="saved orchard coordinates; bounded snapshot reconstruction"))
        self._signatures[profile["id"]] = signature
        self._history[profile["id"]] = history
        return {"latest": latest, "history": history}
