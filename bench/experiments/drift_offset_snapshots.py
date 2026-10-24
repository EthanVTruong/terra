"""Drift/offset detector settings, scored the way the daily replay runs them, but cheaply.

Detection runs on 60-day windows that end at an as-of time (causal sigma, as in
run_detection), on cached expected values of the active model (bench/experiments/
drift_offset_harness.py builds them; the committed results used cimis-v1). Faults: bench
parameter ranges, one per run; checked at as-of = start + 2, 4, 7, 10 days and end + 2 days. False alarms: every clean station, weekly as-of over the season, counting
alarms that fall in that week. Learned offsets are left out (bench_lag14 showed they are not
what limits recall).
"""

from __future__ import annotations

import copy
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from chillwatch import config
from chillwatch.context import get_context
from chillwatch.demo.faults import inject, random_faults
from chillwatch.qc.detect import detect_station
from chillwatch.timeutil import focus_bounds

SEASONS = ["2023-24", "2024-25"]
N_FAULTS = 160
CACHE = Path(config.data_dir()) / "experiments"
CONFIGS = {
    "spec (hourly sigma, 3x)": {"mean24_basis": "hourly", "mean24_sigma": 3.0},
    "daily sigma, 3x": {"mean24_basis": "daily", "mean24_sigma": 3.0},
    "daily sigma, 4x": {"mean24_basis": "daily", "mean24_sigma": 4.0},
    "daily sigma, 5x": {"mean24_basis": "daily", "mean24_sigma": 5.0},
    "daily sigma, 6x": {"mean24_basis": "daily", "mean24_sigma": 6.0},
}


def window(series, exp, hours, as_of):
    w0 = as_of - pd.Timedelta(days=60) + pd.Timedelta(hours=1)
    m = (hours >= w0) & (hours <= as_of)
    return hours[m], series[0][m], series[1][m], exp[m]


def run_one(job):
    sid, hrs, temp, qc, exp_arr, as_of, overrides = job
    th = copy.deepcopy(config.thresholds())
    th["residual"].update(overrides)
    exp = pd.DataFrame(exp_arr, columns=["expected_f", "n_neighbors", "nbr_median"])
    dets, _ = detect_station(sid, hrs, temp, qc, exp, as_of, as_of - pd.Timedelta(days=7), th=th)
    return [(d.type, d.evidence.get("alarm_ts") or str(d.start_ts)) for d in dets if d.type in ("drift", "offset")]


def main(out_path: str) -> None:
    ctx = get_context()
    st = ctx.region_stations()
    results: dict = {n: {} for n in CONFIGS}
    results["model"] = ctx.model.version
    for si, season in enumerate(SEASONS):
        a, b = focus_bounds(season)
        exp_all = pd.read_parquet(CACHE / f"expected_{ctx.model.version}_{season}.parquet")
        r = ctx.store.readings(a, b, st["station_id"].tolist())
        hours = pd.date_range(a, b, freq="h")
        cov = r.groupby("station_id")["temp_f"].apply(lambda s: s.notna().mean())
        cand = cov[cov > 0.8].index.astype(int).tolist()
        specs = random_faults(cand, a, b, N_FAULTS, seed=100 + si, types=("drift", "offset"))
        ser, exs = {}, {}
        for sid in cand:
            rs = r[r["station_id"] == sid].set_index("ts").reindex(hours)
            ser[sid] = (rs["temp_f"].to_numpy(), rs["qc_base"].fillna("missing").to_numpy())
            exs[sid] = exp_all[exp_all["station_id"] == sid].set_index("ts").reindex(hours)[
                ["expected_f", "n_neighbors", "nbr_median"]].to_numpy()
        weeks = [a + pd.Timedelta(days=d) for d in range(35, int((b - a).days), 7)]
        jobs, meta = [], []
        for name, ov in CONFIGS.items():
            for sid in cand:
                for t in weeks:
                    h, tp, q, e = window(ser[sid], exs[sid], hours, t)
                    jobs.append((sid, h, tp, q, e, t, ov))
                    meta.append((name, "clean", sid, t, None))
            for spec in specs:
                sid = int(spec["station"])
                one = pd.DataFrame({"station_id": sid, "ts": hours, "temp_f": ser[sid][0], "qc_base": ser[sid][1],
                                    "qc_status": ser[sid][1]})
                faulty, labels = inject(one, [spec])
                lab = labels[0]
                fs = (faulty["temp_f"].to_numpy(), faulty["qc_base"].to_numpy())
                for t in [lab.start_ts + pd.Timedelta(days=d) for d in (2, 4, 7, 10)] + [lab.end_ts + pd.Timedelta(days=2)]:
                    t = min(t.floor("h"), b)
                    h, tp, q, e = window(fs, exs[sid], hours, t)
                    jobs.append((sid, h, tp, q, e, t, ov))
                    meta.append((name, "fault", sid, t, lab))
        print(f"{season}: {len(jobs)} runs", file=sys.stderr, flush=True)
        with ProcessPoolExecutor(max_workers=2) as ex:
            outs = list(ex.map(run_one, jobs, chunksize=16))
        for name in CONFIGS:
            fa = 0
            for (n, kind, _sid, t, _lab), o in zip(meta, outs, strict=True):
                if n == name and kind == "clean":
                    fa += sum(1 for _, al in o if t - pd.Timedelta(days=7) < pd.Timestamp(al) <= t)
            months = len(cand) * len(weeks) * 7 / 30.44
            hit: dict = {}
            for (n, kind, _sid, _t, lab), o in zip(meta, outs, strict=True):
                if n != name or kind != "fault":
                    continue
                key = (lab.station_id, lab.start_ts)
                rec = hit.setdefault(key, {"type": lab.type, "mag": abs(lab.params.get("delta_f", lab.params.get(
                    "max_delta_f", np.nan))), "first": None, "kind": None, "start": lab.start_ts})
                for typ, al in o:
                    al = pd.Timestamp(al)
                    inside = lab.start_ts - pd.Timedelta(hours=24) <= al <= lab.end_ts + pd.Timedelta(hours=48)
                    if inside and (rec["first"] is None or al < rec["first"]):
                        rec["first"], rec["kind"] = al, typ
            summ = {"false_alarms_per_station_month": round(fa / months, 4)}
            for t in ("drift", "offset"):
                rows = [v for v in hit.values() if v["type"] == t]
                det = [v for v in rows if v["first"] is not None]
                summ[t] = {"n": len(rows), "recall": round(len(det) / max(len(rows), 1), 3),
                           "type_correct": round(sum(v["kind"] == t for v in det) / max(len(rows), 1), 3),
                           "median_hours_to_detect": float(np.median([(v["first"] - v["start"]) / pd.Timedelta(hours=1)
                                                                      for v in det])) if det else None,
                           "recall_mag_lt_4F": round(np.mean([v["first"] is not None for v in rows if v["mag"] < 4]), 3)}
            results[name][season] = summ
    Path(out_path).write_text(json.dumps(results, indent=1, default=str))
    print(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
