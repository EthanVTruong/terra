"""Offline source/QC/interpolation/watch demo using labelled saved weather and availability injection."""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pandas as pd

from terra_monitoring.assessment import assess_location
from .service import Monitor
from .store import utc
from .writer import Writer


def run_demo(store, orchard_id="demo-west"):
    root = Path(__file__).resolve().parents[3]
    saved = json.loads((root / "docs/terra/examples/location.request.json").read_text(encoding="utf-8"))
    # The committed case ends at its query hour. Replay its preceding hours,
    # rather than asking the estimator to invent observations after that cutoff.
    start = utc(saved["query"]["valid_utc"]) - timedelta(hours=4)
    clock = [start]
    monitor = Monitor(store, writer=Writer(key="", model=""), clock=lambda: clock[0])
    existing = next((p for p in store.orchards() if p["id"] == orchard_id), None)
    if existing:
        if existing["mode"] != "injected":
            raise ValueError("A demo cannot reuse a live orchard ID")
        return {"mode": "injected", "status": "already_saved", "orchard_id": orchard_id,
                "messages": store.messages(orchard_id), "source": "saved historical weather; controlled availability injection"}
    profile = monitor.create_orchard("Demo Nonpareil", orchard_id=orchard_id, mode="injected",
                                    **{k: saved["query"][k] for k in ("lat", "lon", "elevation_ft")})
    observations, stations = pd.DataFrame(saved["observations"]), pd.DataFrame(saved["stations"])

    def evaluate(at, missing=False):
        query = dict(saved["query"])
        query.update(valid_utc=at.isoformat(), as_of=at.isoformat(), location_id=orchard_id)
        sample = observations
        if missing:
            sample = observations[observations.station_id.isin(sorted(observations.station_id.unique())[:2])]
        return assess_location(**query, observations=sample, stations=stations,
            snapshot_id=saved["snapshot_id"], data_mode="injected")

    phases = []
    for offset, missing in [(0, False), (1, True), (2, False), (3, False), (4, False)]:
        clock[0] = start + timedelta(hours=offset)
        result = evaluate(clock[0], missing)
        applied = monitor.apply(orchard_id, {"latest": result}, now=clock[0])
        monitor.flush(orchard_id, now=clock[0])
        phases.append({"hour": clock[0].isoformat(), "availability_injected": missing,
                       "estimate_status": result["location_estimate"]["status"], "new_events": applied["new_events"]})
    return {"mode": "injected", "status": "completed", "orchard_id": orchard_id,
            "source": "saved historical weather -> real frozen QC -> Nathan estimator -> persistent events -> local messages",
            "phases": phases, "messages": store.messages(orchard_id),
            "note": "All messages are labelled demo replay. No provider, LLM, SMS or model-training request was made."}
