"""Reproduce the cached March 16–22 station/operator review without networking.

Run from a Terra checkout containing requirements-terra-dev.txt dependencies:
  python reproduce_holdout_review.py --repo-root . --source-dir PATH/TO/normalized

The source directory must contain the existing March 9–15 and March 16–22 JSON
snapshots. No data is fetched, ingested, trained or sent. A fresh temporary output
directory is used unless --output-dir names an empty directory. The original
review ran against ba37a820c988ff08172c2a56f10138f4b6ed6b35.
"""
from __future__ import annotations

import argparse
import collections
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

SOURCE_SHA = "1cccc2507af8e51187c36d9dfc70146cc5c75d2674083eb34a7b976d2766425d"
WARMUP_SHA = "d84985213d73707dc54a6034256f73a8738fb21ccd64605141fd0a7ed48f2385"
ORIGINAL_REVISION = "ba37a820c988ff08172c2a56f10138f4b6ed6b35"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    sys.path.insert(0, str(repo / "backend"))

    import pandas as pd
    import chillwatch.estimate
    import terra_qc.qc
    import terra_monitoring.assessment
    import terra_monitoring.farm_ops.service
    from terra_qc.reference import load_reference, REFERENCE_ID, REFERENCE_SHA256
    from terra_monitoring.assessment import assess_location, _clean, _observations, _operation_gate, _stations, _score
    from terra_monitoring.farm_ops.service import Monitor
    from terra_monitoring.farm_ops.store import Store, utc
    from terra_monitoring.farm_ops.writer import Writer

    started = time.perf_counter()
    source = args.source_dir.resolve() / "2026-03-16_2026-03-22.json"
    warmup_source = args.source_dir.resolve() / "2026-03-09_2026-03-15.json"
    if digest(source) != SOURCE_SHA or digest(warmup_source) != WARMUP_SHA:
        raise ValueError("Cached source bytes differ from the reviewed snapshots")
    output = args.output_dir.resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix="terra-station-holdout-"))
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty output directory; existing evidence is never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    rows, previous = json.loads(source.read_bytes()), json.loads(warmup_source.read_bytes())
    first = min(utc(row["timestamp"]) for row in rows)
    warmup = [row for row in previous if first - timedelta(hours=24) <= utc(row["timestamp"]) < first]
    all_rows = warmup + rows
    reference = load_reference()
    if any(utc(row["timestamp"]) < utc(reference.manifest["validation_end_exclusive"]) for row in all_rows):
        raise ValueError("Held-out inputs overlap calibration")
    geometry = reference.manifest["station_geometry"]["stations"]
    stations = _stations(pd.DataFrame([{"station_id": int(key), "name": value["name"],
        "lat": value["latitude"], "lon": value["longitude"], "elevation_ft": value["elevation_ft"]}
        for key, value in geometry.items()]))
    site = {"lat": 38.55, "lon": -121.78, "elevation_ft": 60.0}
    orchard_id = "operator-march16"
    clock = [first]

    def watch(name):
        monitor = Monitor(Store(output / (name + ".sqlite3")), writer=Writer(key="", model="", voice="text"),
                          clock=lambda: clock[0])
        monitor.create_orchard("Held-out weather control", orchard_id=orchard_id, mode="injected", **site)
        return monitor

    def sample_at(stamp):
        return [row for row in all_rows if utc(row["timestamp"]) <= utc(stamp)]

    def evaluate(sample, stamp, tag):
        return assess_location(**site, valid_utc=stamp, as_of=stamp, observations=pd.DataFrame(sample),
            stations=stations, snapshot_id="operator:" + SOURCE_SHA + ":" + tag, data_mode="injected",
            location_id=orchard_id, reference=reference,
            geometry_source="frozen reference geometry; explicit held-out operator pin")

    def controls(stamp, prefix):
        sample = sample_at(stamp)
        original = next(row["temperature_f"] for row in sample if row["station_id"] == 6 and row["timestamp"] == stamp)
        result = []
        for name in ("original", "minor_plus1", "spike_plus24", "support_loss"):
            clone = json.loads(json.dumps(sample))
            for row in clone:
                if row["timestamp"] != stamp:
                    continue
                if name in {"minor_plus1", "spike_plus24"} and row["station_id"] == 6:
                    row["temperature_f"] += 1 if name == "minor_plus1" else 24
                if name == "support_loss" and row["station_id"] in {139, 212, 235}:
                    row.update(temperature_f=None, quality="missing", availability_reason="simulated_offline")
            packet = evaluate(clone, stamp, name)
            clock[0] = utc(stamp)
            monitor = watch(prefix + name)
            baseline = evaluate(sample, stamp, "case-baseline")
            monitor.apply(orchard_id, {"latest": baseline}, now=clock[0])
            monitor.flush(orchard_id, now=clock[0])
            before = {message["id"] for message in monitor.store.messages(orchard_id)}
            monitor.apply(orchard_id, {"latest": packet}, now=clock[0])
            monitor.flush(orchard_id, now=clock[0])
            messages = [message for message in monitor.store.messages(orchard_id) if message["id"] not in before]
            result.append({"name": name, "timestamp": stamp, "davis_original_f": original,
                "davis_value_f": next(row["temperature_f"] for row in clone if row["station_id"] == 6 and row["timestamp"] == stamp),
                "estimate": packet["location_estimate"], "findings": packet["station_findings"],
                "source_health": packet["data_health"], "new_grower_note_count": len(messages),
                "new_grower_notes": [{key: message[key] for key in ("id", "text", "event_ids")} for message in messages],
                "events": [{key: event[key] for key in ("type", "transition", "facts")} for event in monitor.store.events(orchard_id)]})
        return result

    hours = sorted({row["timestamp"] for row in rows})
    hourly, candidates, findings = [], [], {}
    operation_counts, health_counts, assessment_counts = (collections.Counter() for _ in range(3))
    natural = watch("natural")
    with patch("httpx.Client.send", side_effect=AssertionError("Network prohibited during cached weather review")):
        for index, stamp in enumerate(hours):
            clock[0] = utc(stamp)
            sample = sample_at(stamp)
            packet = evaluate(sample, stamp, "natural")
            natural.apply(orchard_id, {"latest": packet}, now=clock[0])
            natural.flush(orchard_id, now=clock[0])
            cleaned = _operation_gate(_clean(_observations(pd.DataFrame(sample), pd.Timestamp(stamp)),
                                            {int(key) for key in geometry}), stations)
            current = cleaned[cleaned.timestamp.eq(pd.Timestamp(stamp))]
            operation_counts.update(eligible_rows=int(current.operation_available.sum()),
                                    ineligible_rows=int((~current.operation_available).sum()))
            known = cleaned[cleaned.operation_available].copy()
            known["quality"] = known["source_quality"]
            scored = _score(known, reference)
            assessment_counts.update(str(row["status"]) for row in scored[scored.timestamp.eq(pd.Timestamp(stamp))].to_dict("records"))
            health_counts.update(item["reason"] for item in packet["data_health"])
            flags = []
            for item in packet["station_findings"]:
                key = (item["station_id"], item["reason"], item["start_utc"], item["detected_at_utc"])
                entry = findings.setdefault(key, {field: item[field] for field in
                    ("station_id", "reason", "severity", "start_utc", "detected_at_utc")})
                entry.setdefault("first_seen_utc", stamp)
                entry["visible_hours"] = entry.get("visible_hours", 0) + 1
                entry["last_seen_utc"] = stamp
                flags.append({field: item[field] for field in ("station_id", "reason", "severity")})
            estimate = packet["location_estimate"]
            hourly.append({"timestamp": stamp, "status": estimate["status"], "reason": estimate["status_reason"],
                "n_used": estimate["n_used"], "temperature_f": estimate["temperature"], "findings": flags,
                "source_health": packet["data_health"]})
            if not flags and estimate["status"] == "ok" and estimate["n_used"] == 5:
                candidates.append(stamp)
            if (index + 1) % 24 == 0:
                print(json.dumps({"progress_hours": index + 1, "total_hours": len(hours),
                                  "seconds": round(time.perf_counter() - started, 1)}), flush=True)
        for entry in findings.values():
            stamp = entry["detected_at_utc"]
            item = next(item for item in evaluate(sample_at(stamp), stamp, "natural-detected")["station_findings"]
                        if item["station_id"] == entry["station_id"])
            entry["detector_evidence"] = [{key: evidence.get(key) for key in ("timestamp", "temperature_f", "expected_f",
                "residual_f", "level", "neighbor_count", "sustained_low_residual_f", "sustained_high_residual_f",
                "extreme_abs_residual_f", "source_quality", "original_source_flag")} for evidence in item["evidence"]]
        boundary_hour = next(stamp for stamp in candidates if stamp >= "2026-03-16T11:00:00+00:00")
        boundary_controls = controls(boundary_hour, "boundary-")
        for chosen in (stamp for stamp in candidates if stamp > boundary_hour):
            clone = json.loads(json.dumps(sample_at(chosen)))
            for row in clone:
                if row["timestamp"] == chosen and row["station_id"] == 6:
                    row["temperature_f"] += 1
            if not evaluate(clone, chosen, "minor-candidate")["station_findings"]:
                break
        else:
            raise ValueError("No clean minor-control hour")
        scenarios = controls(chosen, "clean-")

    events = natural.store.events(orchard_id)
    revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    report = {"schema_version": "terra-independent-cached-holdout-v1", "source_file": source.name, "source_sha256": SOURCE_SHA,
        "rows": len(rows), "warmup_rows": len(warmup), "first_utc": hours[0], "last_utc": hours[-1],
        "causal_hourly_assessments": len(hours), "causality": "Only observations at or before each valid hour; prior24h warmup; no future rows",
        "reference_id": REFERENCE_ID, "reference_sha256": REFERENCE_SHA256,
        "training_end_exclusive": reference.manifest["train_end_exclusive"], "calibration_end_exclusive": reference.manifest["validation_end_exclusive"],
        "quality_counts": dict(collections.Counter(row["quality"] for row in rows)),
        "source_flag_counts": dict(collections.Counter(row["source_flag"] for row in rows)),
        "estimate_status_counts": dict(collections.Counter(item["status"] for item in hourly)),
        "estimate_reason_counts": dict(collections.Counter(item["reason"] for item in hourly)),
        "qc_row_status_counts": dict(assessment_counts), "operation_availability": dict(operation_counts),
        "operation_caveat": "Historical operation epochs are unverified; absent dates use the existing permissive gate",
        "natural_suspected_findings": list(findings.values()), "natural_source_health_reason_counts": dict(health_counts),
        "natural_grower_transition_counts": dict(collections.Counter(item["type"] + ":" + item["transition"] for item in events)),
        "selected_control_utc": chosen, "clean_candidate_hours": len(candidates), "scenarios": scenarios, "hourly": hourly,
        "boundary_probe": {"timestamp": boundary_hour, "description": "A one-degree change can extend an existing extreme episode when the current row crosses its sustained threshold; episode severity differs from current-row level", "scenarios": boundary_controls},
        "source_unchanged_after_checks": digest(source) == SOURCE_SHA,
        "side_effects": {"network_calls": 0, "live_runtime_changes": False, "sms": False, "llm": False, "training": False, "ingestion": False},
        "evidence_limit": "Previously inspected cached March holdout; suspected findings are not confirmed natural sensor-fault labels",
        "execution_source_revision": revision, "original_review_source_revision": ORIGINAL_REVISION,
        "execution_module_sha256": {module.__name__: digest(module.__file__) for module in
            (chillwatch.estimate, terra_qc.qc, terra_monitoring.assessment, terra_monitoring.farm_ops.service)},
        "elapsed_seconds": round(time.perf_counter() - started, 3)}
    full_path = output / "station-holdout-review.json"
    full_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    summary = {key: value for key, value in report.items() if key not in {"scenarios", "hourly", "boundary_probe"}}
    summary["scenarios"] = [{"name": item["name"], "timestamp": item["timestamp"], "original_f": item["davis_original_f"],
        "value_f": item["davis_value_f"], "estimate_status": item["estimate"]["status"], "estimate_reason": item["estimate"]["status_reason"],
        "n_used": item["estimate"]["n_used"], "station_findings": [{key: flag[key] for key in ("station_id", "reason", "severity")}
        for flag in item["findings"]], "new_grower_note_count": item["new_grower_note_count"]} for item in scenarios]
    summary["boundary_probe"] = {key: value for key, value in report["boundary_probe"].items() if key != "scenarios"}
    summary["full_evidence"] = {"path": str(full_path), "sha256": digest(full_path)}
    summary_path = output / "station-holdout-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(summary_path), "sha256": digest(summary_path),
                      "full_evidence": str(full_path), "full_sha256": digest(full_path)}, indent=2))


if __name__ == "__main__":
    main()
