"""Combine per-season bench JSONs (bench_season.py) into one detection / false-alarm summary per setting.

usage: combine_bench.py label=run1.json,run2.json [label=...] > out.json
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

TYPES = ["drift", "offset", "spike", "stuck", "outage", "clock_shift"]


def season_of(ts):
    y, m = int(ts[:4]), int(ts[5:7])
    y0 = y if m >= 9 else y - 1
    return f"{y0}-{(y0 + 1) % 100:02d}"


def summarize(paths):
    faults, fa_months, fa_by_type, seasons, per_season = [], 0.0, defaultdict(int), [], {}
    chill = defaultdict(list)
    for p in paths:
        d = json.loads(Path(p).read_text())
        seasons += d["seasons"]
        faults += d["faults"]
        for s, bs in zip(d["seasons"], d["false_alarms"]["by_season"], strict=True):
            fa_months += bs["uninjected_station_months"]
            for k, v in bs["by_type_uninjected"].items():
                fa_by_type[k] += v
            do = sum(bs["by_type_uninjected"].get(k, 0) for k in ("drift", "offset"))
            per_season[s] = {
                "model": d["model_version"],
                **{t: [sum(f["detected"] for f in d["faults"] if f["type"] == t and season_of(f["start_ts"]) == s),
                       sum(f["type"] == t and season_of(f["start_ts"]) == s for f in d["faults"])]
                   for t in ("drift", "offset")},
                "fa_per_station_month": round(bs["per_station_month_uninjected"], 3),
                "fa_drift_offset_per_station_month": round(do / bs["uninjected_station_months"], 3),
            }
        ch = d.get("chill_hours", {})
        for k in ("mean_abs_error_unrepaired", "mean_abs_error_repaired"):
            if k in ch:
                chill[k].append((ch[k], ch["station_seasons"]))
    det = {}
    for t in TYPES:
        rows = [f for f in faults if f["type"] == t]
        hit = [f for f in rows if f["detected"]]
        det[t] = {"n": len(rows), "detected": len(hit), "recall": round(len(hit) / max(len(rows), 1), 3),
                  "type_correct": sum(bool(f.get("type_correct")) for f in hit),
                  "median_hours_to_detect": float(np.median([f["hours_to_detect"] for f in hit])) if hit else None}
    total = sum(fa_by_type.values())
    return {
        "seasons": seasons,
        "detection": det,
        "false_alarms_per_station_month": round(total / fa_months, 3),
        "drift_offset_false_alarms_per_station_month": round((fa_by_type["drift"] + fa_by_type["offset"]) / fa_months, 3),
        "false_alarms_by_type": dict(fa_by_type),
        "uninjected_station_months": round(fa_months, 2),
        "per_season": per_season,
        "chill_on_faulted": {k: round(sum(a * n for a, n in v) / sum(n for _, n in v), 2) for k, v in chill.items()},
    }


out = {}
for arg in sys.argv[1:]:
    label, paths = arg.split("=", 1)
    out[label] = summarize(paths.split(","))
print(json.dumps(out, indent=1))
