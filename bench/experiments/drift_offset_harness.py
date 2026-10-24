"""Fast drift/offset detector tuning: one detection pass per station-season, no day-by-day replay.

Expected values (model, source-QC neighbors) are computed once per held-out season and cached.
Each injected fault (bench parameter ranges) goes into one station's clean series, and the
detector runs once with as_of = season end. False alarms come from the same detector on every
clean station-season. Tune on 2023-24, check on 2024-25, then confirm with `chillwatch bench`
(the full replay also includes weekly offset learning, which this harness leaves out).
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
N_FAULTS = 160  # per season, half drift, half offset
CACHE = Path(config.data_dir()) / "experiments"
CONFIGS = {
    "spec (hourly sigma, 3x)": {"mean24_basis": "hourly", "mean24_sigma": 3.0, "cusum_h": 8.0},
    "hourly sigma, 2x": {"mean24_basis": "hourly", "mean24_sigma": 2.0, "cusum_h": 8.0},
    "daily sigma, 3x": {"mean24_basis": "daily", "mean24_sigma": 3.0, "cusum_h": 8.0},
    "daily sigma, 4x": {"mean24_basis": "daily", "mean24_sigma": 4.0, "cusum_h": 8.0},
    "daily sigma, 5x": {"mean24_basis": "daily", "mean24_sigma": 5.0, "cusum_h": 8.0},
    "daily sigma, 4x, h16": {"mean24_basis": "daily", "mean24_sigma": 4.0, "cusum_h": 16.0},
    "daily sigma, 5x, h16": {"mean24_basis": "daily", "mean24_sigma": 5.0, "cusum_h": 16.0},
}


def season_inputs(season: str):
    ctx = get_context()
    a, b = focus_bounds(season)
    st = ctx.region_stations()
    r = ctx.store.readings(a, b, st["station_id"].tolist())
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"expected_{season}.parquet"
    if f.exists():
        exp = pd.read_parquet(f)
    else:
        ctx.precompute_expected(a, b)
        exp = ctx._frozen[["station_id", "ts", "expected_f", "n_neighbors", "nbr_median"]].copy()
        ctx._frozen = None
        exp.to_parquet(f, index=False)
    return a, b, r, exp


def run_one(job):
    sid, hours, temp, qc, exp_arr, a, b, overrides = job
    th = copy.deepcopy(config.thresholds())
    th["residual"].update(overrides)
    exp = pd.DataFrame(exp_arr, columns=["expected_f", "n_neighbors", "nbr_median"])
    dets, _ = detect_station(sid, hours, temp, qc, exp, b, a, th=th)
    return [(d.type, d.start_ts, d.end_ts, d.evidence.get("alarm_ts")) for d in dets if d.type in ("drift", "offset")]


def main(out_path: str) -> None:
    results: dict[str, dict] = {name: {} for name in CONFIGS}
    for si, season in enumerate(SEASONS):
        a, b, r, exp = season_inputs(season)
        hours = pd.date_range(a, b, freq="h")
        cov = r.groupby("station_id")["temp_f"].apply(lambda s: s.notna().mean())
        cand = cov[cov > 0.8].index.astype(int).tolist()
        specs = random_faults(cand, a, b, N_FAULTS, seed=100 + si, types=("drift", "offset"))
        series = {}
        for sid in cand:
            rs = r[r["station_id"] == sid].set_index("ts").reindex(hours)
            es = exp[exp["station_id"] == sid].set_index("ts").reindex(hours)
            series[sid] = (rs, es)
        jobs, meta = [], []
        for name, ov in CONFIGS.items():
            for sid in cand:  # clean runs (false alarms)
                rs, es = series[sid]
                jobs.append((sid, hours, rs["temp_f"].to_numpy(), rs["qc_base"].fillna("missing").to_numpy(),
                             es[["expected_f", "n_neighbors", "nbr_median"]].to_numpy(), a, b, ov))
                meta.append((name, "clean", sid, None))
            for spec in specs:  # one fault per run
                sid = int(spec["station"])
                rs, _ = series[sid]
                one = rs.reset_index().rename(columns={"index": "ts"}).assign(station_id=sid)
                faulty, labels = inject(one, [spec])
                fs = faulty.set_index("ts").reindex(hours)
                _, es = series[sid]
                jobs.append((sid, hours, fs["temp_f"].to_numpy(), fs["qc_base"].fillna("missing").to_numpy(),
                             es[["expected_f", "n_neighbors", "nbr_median"]].to_numpy(), a, b, ov))
                meta.append((name, "fault", sid, labels[0]))
        print(f"{season}: {len(jobs)} detector runs", file=sys.stderr, flush=True)
        with ProcessPoolExecutor(max_workers=3) as ex:
            outs = list(ex.map(run_one, jobs, chunksize=8))
        obs_months = {sid: series[sid][0]["temp_f"].notna().sum() / 24 / 30.44 for sid in cand}
        for name in CONFIGS:
            fa = sum(len(o) for (n, kind, _, _), o in zip(meta, outs, strict=True) if n == name and kind == "clean")
            months = sum(obs_months.values())
            rec = {"drift": [], "offset": []}
            for (n, kind, _sid, lab), o in zip(meta, outs, strict=True):
                if n != name or kind != "fault":
                    continue
                hit = None
                for typ, s, e, alarm in o:
                    al = pd.Timestamp(alarm) if alarm else pd.Timestamp(s)
                    end = pd.Timestamp(e) if e is not None else b
                    if al >= lab.start_ts - pd.Timedelta(hours=24) and al <= lab.end_ts + pd.Timedelta(hours=48) \
                            and end >= lab.start_ts:
                        hit = (typ, (al - lab.start_ts) / pd.Timedelta(hours=1))
                        break
                mag = abs(lab.params.get("delta_f", lab.params.get("max_delta_f", np.nan)))
                rec[lab.type].append({"hit": hit is not None, "type_ok": bool(hit and hit[0] == lab.type),
                                      "ttd_h": hit[1] if hit else None, "mag": mag})
            summ = {"false_alarms_per_station_month": round(fa / months, 4), "station_months": round(months, 1)}
            for t, rows in rec.items():
                df = pd.DataFrame(rows)
                summ[t] = {"n": len(df), "recall": round(float(df["hit"].mean()), 3),
                           "type_correct": round(float(df["type_ok"].mean()), 3),
                           "median_hours_to_detect": float(df["ttd_h"].median()) if df["hit"].any() else None,
                           "recall_mag_lt_4F": round(float(df[df["mag"] < 4]["hit"].mean()), 3)
                           if (df["mag"] < 4).any() else None}
            results[name][season] = summ
    Path(out_path).write_text(json.dumps(results, indent=1, default=str))
    print(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
