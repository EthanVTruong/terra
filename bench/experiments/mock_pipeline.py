"""End-to-end check of the pipeline on synthetic (mock) data where the truth is known.

A 30-station synthetic network (demo/synthetic.py: seasonal + weather + lapse/inversions +
station bias + noise). Five stations are held out as "orchards": the pipeline never sees them,
their series is the truth. Season A (2021-22) trains the model and fits the physics; season B
(2022-23) is scored. Checks, each with a pass/fail threshold:

1. interpolation at the held-out sites (estimate_location, Terra interface) vs nearest-station
   and plain-average baselines; lapse on/off at the foothill site; chill-hour error
2. calibrated p10-p90 coverage (fit-physics bins) at the held-out sites
3. hour-of-day offsets recover an injected night/day bias pattern
4. detection in a day-by-day replay with injected spike / stuck / offset / drift / outage
5. Terra handoff: findings from detection keep a faulty neighbor out of the estimate
6. gap filling of the outage vs truth

NOT real data; numbers here only show the code paths work and are consistent.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["CHILLWATCH_DATA_DIR"] = tempfile.mkdtemp(prefix="mock_pipeline_")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from chillwatch import config  # noqa: E402
from chillwatch.bench.run import match_faults  # noqa: E402
from chillwatch.context import Context  # noqa: E402
from chillwatch.demo.faults import inject  # noqa: E402
from chillwatch.demo.synthetic import make_readings, make_stations  # noqa: E402
from chillwatch.estimate import estimate_location  # noqa: E402
from chillwatch.pipeline import replay  # noqa: E402
from chillwatch.qc.model import chill_hours, chill_mask, set_offset_from_window, train  # noqa: E402
from chillwatch.repair import physics_fit  # noqa: E402
from chillwatch.store import MemoryStore  # noqa: E402

T = pd.Timestamp
A0, A1 = T("2021-10-01 09:00", tz="UTC"), T("2022-04-01 08:00", tz="UTC")
B0, B1 = T("2022-10-01 09:00", tz="UTC"), T("2023-04-01 08:00", tz="UTC")
report: dict = {"checks": {}}


def check(name: str, ok: bool, **numbers) -> None:
    report["checks"][name] = {"pass": bool(ok), **numbers}
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {numbers}", file=sys.stderr, flush=True)


def observations(r: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"station_id": r["station_id"].astype(int).to_numpy(),
                         "timestamp": pd.to_datetime(r["ts"], utc=True),
                         "temperature_f": r["temp_f"].to_numpy(),
                         "quality": np.where(r["qc_base"].eq("ok"), "usable", r["qc_base"].astype(str)),
                         "source_flag": ""})


# ------------------------------------------------------------------ the synthetic world
stations = make_stations(30, seed=0)
truth_A = make_readings(stations, "2021-10-01 09:00", "2022-04-01 08:00", seed=1, missing_rate=0.0)
truth_B = make_readings(stations, "2022-10-01 09:00", "2023-04-01 08:00", seed=2, missing_rate=0.0)
rest = stations[~stations["station_id"].isin([6, 70, 71, 39, 5, 12])]
held = [int(stations["station_id"].iloc[7])] + [int(s) for s in rest["station_id"].iloc[[3, 8, 13, 18]]]
net = stations[~stations["station_id"].isin(held)].reset_index(drop=True)
netA = truth_A[~truth_A["station_id"].isin(held)].reset_index(drop=True)
netB = truth_B[~truth_B["station_id"].isin(held)].reset_index(drop=True)
report["held_out"] = {int(s): {"elevation_ft": float(stations.set_index("station_id").loc[s, "elevation_ft"])}
                      for s in held}
print(f"network {len(net)} stations; held out {held}", file=sys.stderr, flush=True)

# ------------------------------------------------------------------ fit physics + bins (season A), train model
both = Context(MemoryStore(net, pd.concat([netA, netB], ignore_index=True)))
fit = physics_fit.fit(both, ["2021-22"], ["2022-23"])
th = config.thresholds()
th["baseline"]["unseen_lapse_shrink"] = fit["best"]["lapse_shrink"]  # as one would after fit-physics
th["baseline"]["unseen_dz_scale_ft"] = fit["best"]["dz_scale_ft"]
(config.data_dir() / "models").mkdir(parents=True, exist_ok=True)
(config.data_dir() / "models" / "physics_fit.json").write_text(json.dumps(fit, default=str))
th["estimate"]["intervals"] = True
report["physics_fit"] = {"best": fit["best"], "check_mae_f": fit["check"]["best"]["mae_f"],
                         "check_coverage": fit["check"]["p10_p90_coverage"]}
model = train(MemoryStore(net, netA), seasons=["2021-22"], version="mock", loso_folds=2,
              out_dir=config.data_dir() / "models", activate=False)

# ------------------------------------------------------------------ 1-2. interpolation at held-out sites
hours = pd.date_range(B0, B1, freq="h")
obsB = observations(netB)
geo = net[["station_id", "lat", "lon", "elevation_ft"]]
from chillwatch.qc.neighbors import haversine_km  # noqa: E402

rows = {}
for sid in held:
    s = stations.set_index("station_id").loc[sid]
    est = estimate_location(float(s.lat), float(s.lon), float(s.elevation_ft), hours, B1, obsB, geo,
                            location_id=f"MOCK-{sid}", geometry_source="synthetic")
    tr = truth_B[truth_B["station_id"] == sid].set_index("ts")["temp_f"].reindex(hours).to_numpy()
    t = est["temperature"].to_numpy()
    ok = np.isfinite(t) & np.isfinite(tr)
    d = haversine_km(s.lat, s.lon, geo["lat"], geo["lon"])
    near = int(geo["station_id"].iloc[int(np.argmin(d))])
    nb = netB[netB["station_id"] == near].set_index("ts")["temp_f"].reindex(hours).to_numpy()
    six = geo["station_id"].iloc[np.argsort(d)[:6]].tolist()
    avg = netB[netB["station_id"].isin(six)].groupby("ts")["temp_f"].mean().reindex(hours).to_numpy()
    cm = chill_mask(hours) & ok
    th["estimate"]["lapse_min_span_ft"] = 1e9  # lapse off, for comparison
    off = estimate_location(float(s.lat), float(s.lon), float(s.elevation_ft), hours, B1, obsB, geo)
    th["estimate"]["lapse_min_span_ft"] = 500
    to = off["temperature"].to_numpy()
    rows[sid] = {
        "elevation_ft": float(s.elevation_ft), "ok_hours": int(ok.sum()),
        "mae_f": round(float(np.abs(t[ok] - tr[ok]).mean()), 3),
        "mae_lapse_off_f": round(float(np.abs(to[ok] - tr[ok]).mean()), 3),
        "mae_nearest_station_f": round(float(np.abs(nb[ok] - tr[ok]).mean()), 3),
        "mae_six_station_mean_f": round(float(np.abs(avg[ok] - tr[ok]).mean()), 3),
        "lapse_hours_share": round(float((est["method"] == "idw_lapse").mean()), 3),
        "p10_p90_coverage": round(float(((tr >= est["p10"]) & (tr <= est["p90"]))[ok].mean()), 3),
        "chill_true_h": chill_hours(tr[cm]), "chill_est_h": chill_hours(t[cm]),
    }
report["interpolation"] = rows
mae = np.mean([r["mae_f"] for r in rows.values()])
base = np.mean([min(r["mae_nearest_station_f"], r["mae_six_station_mean_f"]) for r in rows.values()])
check("interpolation beats nearest-station and plain-average baselines", mae < base, mae_f=round(mae, 3),
      best_baseline_mae_f=round(base, 3))
hill = rows[held[0]]
check("lapse helps at the foothill site", hill["mae_f"] < hill["mae_lapse_off_f"],
      elevation_ft=hill["elevation_ft"], mae_f=hill["mae_f"], mae_lapse_off_f=hill["mae_lapse_off_f"])
cov = np.mean([r["p10_p90_coverage"] for r in rows.values()])
check("p10-p90 covers ~80% at held-out sites", 0.70 <= cov <= 0.90, coverage=round(float(cov), 3),
      on_fit_check_season=fit["check"]["p10_p90_coverage"]["all"])
cerr = [abs(r["chill_est_h"] - r["chill_true_h"]) for r in rows.values()]
check("chill hours at held-out sites within 15% of truth",
      all(e <= 0.15 * r["chill_true_h"] for e, r in zip(cerr, rows.values(), strict=True)),
      abs_error_h=cerr, true_h=[r["chill_true_h"] for r in rows.values()])

# ------------------------------------------------------------------ 3. hour-of-day offsets recover a known pattern
sid = int(net["station_id"].iloc[10])
pattern = np.array([2.5 if (h >= 19 or h <= 6) else 0.5 for h in range(24)])  # by PST hour
w = (B0, B0 + pd.Timedelta(days=28))
clean = Context(MemoryStore(net, netB), model)
base_off = set_offset_from_window(sid, *w, ctx=clean, kind="learned")
shifted = netB.copy()
m = shifted["station_id"] == sid
pst_h = (pd.DatetimeIndex(shifted.loc[m, "ts"]).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=8)).hour
shifted.loc[m, "temp_f"] = shifted.loc[m, "temp_f"].to_numpy() + pattern[pst_h]
off = set_offset_from_window(sid, *w, ctx=Context(MemoryStore(net, shifted), model), kind="learned")
rec = np.array(json.loads(off["hourly_f"])) - np.array(json.loads(base_off["hourly_f"]))
err = float(np.abs(rec - pattern).max())
check("hour-of-day offsets recover an injected bias pattern", err < 0.3, max_abs_error_f=round(err, 3),
      recovered_night=round(float(rec[[0, 3, 22]].mean()), 2), recovered_day=round(float(rec[[10, 13, 16]].mean()), 2))

# ------------------------------------------------------------------ 4. detection replay with injected faults
ids = net["station_id"].astype(int).tolist()
faults = [
    {"type": "spike", "station": ids[1], "start": "2022-11-10T12:00Z", "hours": 2, "delta_f": 20.0},
    {"type": "stuck", "station": ids[4], "start": "2022-11-20T08:00Z", "hours": 24},
    {"type": "offset", "station": ids[9], "start": "2022-12-01T08:00Z", "delta_f": 4.0, "end": "2022-12-15T08:00Z"},
    {"type": "drift", "station": ids[13], "start": "2022-12-20T08:00Z", "max_delta_f": 5.0, "days": 7, "hold_days": 3},
    {"type": "outage", "station": ids[16], "start": "2023-01-05T08:00Z", "days": 3},
]
first_day, last_day = T("2022-11-01"), T("2023-01-31")
faulty, labels = inject(netB.copy(), faults)
pre = Context(MemoryStore(net, faulty), model)
pre.precompute_expected(B0, T("2023-02-01 08:00", tz="UTC"))
rc = Context(MemoryStore(net), model)
rc.freeze_from(pre)
res = replay(rc, faulty, first_day, last_day)
matches = match_faults(labels, res.runs)
det = {}
for lab, mt in zip(labels, matches, strict=True):
    det[lab.type] = {"station": lab.station_id, "detected": bool(mt.get("detected")),
                     "detected_as": mt.get("detected_as"), "hours_to_detect": mt.get("hours_to_detect")}
report["detection"] = det
faulted = {f["station"] for f in faults}
fa = {(d.station_id, d.type, str(pd.Timestamp(d.start_ts).date())) for _, ds in res.runs for d in ds
      if d.station_id not in faulted and d.station_id != 0}
months = len(ids) * (last_day - first_day).days / 30.44
check("spike, stuck and outage detected", all(det[k]["detected"] for k in ("spike", "stuck", "outage")),
      **{k: det[k]["detected"] for k in ("spike", "stuck", "outage")})
check("4 F offset and 5 F drift detected", det["offset"]["detected"] and det["drift"]["detected"],
      offset=det["offset"], drift=det["drift"])
check("false alarms on clean stations below 1 per station-month", len(fa) / months < 1.0,
      per_station_month=round(len(fa) / months, 3), alarms=sorted(fa)[:10])

# ------------------------------------------------------------------ 5. Terra handoff: findings keep a fault out
after = rc.store.readings(B0, T("2023-02-01 08:00", tz="UTC"))
flagged = after[after["qc_status"].isin(["invalid", "suspect"])]
findings = pd.DataFrame({"station_id": flagged["station_id"].astype(int), "timestamp": flagged["ts"],
                         "reason": "chillwatch_qc_as_mock_terra_finding"})
spike_station = faults[0]["station"]
geo_s = geo.set_index("station_id")
target = min(held, key=lambda h: float(haversine_km(stations.set_index("station_id").loc[h, "lat"],
                                                    stations.set_index("station_id").loc[h, "lon"],
                                                    geo_s.loc[spike_station, "lat"], geo_s.loc[spike_station, "lon"])))
s = stations.set_index("station_id").loc[target]
win = pd.date_range(T("2022-11-10T12:00Z"), periods=2, freq="h")
tr = truth_B[truth_B["station_id"] == target].set_index("ts")["temp_f"].reindex(win).to_numpy()
args = (float(s.lat), float(s.lon), float(s.elevation_ft), win, win[-1])
e_clean = estimate_location(*args, observations(netB), geo)["temperature"].to_numpy()
e_raw = estimate_location(*args, observations(faulty), geo)["temperature"].to_numpy()
e_flag = estimate_location(*args, observations(faulty), geo, findings)["temperature"].to_numpy()
used_spike = spike_station in estimate_location(*args, observations(faulty), geo)["used_station_ids"].iloc[0]
check("findings remove a spiking neighbor from the estimate",
      float(np.abs(e_flag - e_clean).max()) < 1.0 and (not used_spike or float(np.abs(e_raw - tr).max()) >
                                                         float(np.abs(e_flag - tr).max())),
      site=target, spike_neighbor_used=used_spike, err_clean_f=np.round(np.abs(e_clean - tr), 2).tolist(),
      err_unflagged_f=np.round(np.abs(e_raw - tr), 2).tolist(), err_flagged_f=np.round(np.abs(e_flag - tr), 2).tolist())

# ------------------------------------------------------------------ 6. gap filling of the outage
o = faults[4]
o0 = T(o["start"])
o1 = o0 + pd.Timedelta(days=o["days"]) - pd.Timedelta(hours=1)
fl = after[(after["station_id"] == o["station"]) & (after["ts"] >= o0) & (after["ts"] <= o1)].set_index("ts")
tr = truth_B[truth_B["station_id"] == o["station"]].set_index("ts")["temp_f"].reindex(fl.index)
filled = fl["used_temp_f"].notna() & fl["is_estimated"].astype(bool)
gm = float((fl.loc[filled, "used_temp_f"] - tr[filled]).abs().mean()) if filled.any() else float("nan")
check("3-day outage filled, MAE under 3 F", bool(filled.mean() > 0.95) and gm < 3.0,
      filled_share=round(float(filled.mean()), 3), mae_f=round(gm, 3))

report["summary"] = {"passed": sum(c["pass"] for c in report["checks"].values()), "of": len(report["checks"])}
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mock_pipeline.json")
out.write_text(json.dumps(report, indent=1, default=str))
print(json.dumps(report["summary"]))
