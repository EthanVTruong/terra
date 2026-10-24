"""Calibrated p10-p90 ranges and summer accuracy of estimate_location on Terra's five stations.

Each Terra station is estimated from the other four (held out entirely), with intervals on and
all months allowed, in three windows: Oct-Mar 2024-25 and Apr-Aug 2025 (held-out season; the
physics and its bins were fitted on 2018-19..2022-23) and March 2026. Truth is Terra-style
eligible readings (valid, blank CIMIS flag). Also the six ASOS sites near Davis (Oct-Mar data
only), estimated from Terra's five stations. Baseline: unweighted median of the other peers (>=3).
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from chillwatch import config
from chillwatch.context import get_context
from chillwatch.estimate import estimate_location
from chillwatch.store import SqlStore

ET = [6, 226, 139, 212, 235]
T = pd.Timestamp
H1 = pd.Timedelta(hours=1)
th = config.thresholds()
th["estimate"]["intervals"] = True
th["estimate"]["validated_months"] = list(range(1, 13))
ctx = get_context()
st = ctx.region_stations()
geo = st[st["station_id"].isin(ET)][["station_id", "lat", "lon", "elevation_ft"]]


def obs_of(r):
    return pd.DataFrame({"station_id": r["station_id"].astype(int).to_numpy(),
                         "timestamp": pd.to_datetime(r["ts"], utc=True),
                         "temperature_f": r["temp_f"].to_numpy(),
                         "quality": np.where(r["qc_base"].eq("ok"), "usable", r["qc_base"].astype(str)),
                         "source_flag": r["qc_source"].fillna("").astype(str).str.strip().to_numpy()})


def score(est, truth, base=None):
    t, lo, hi = est["temperature"].to_numpy(), est["p10"].to_numpy(), est["p90"].to_numpy()
    ok = np.isfinite(t) & np.isfinite(truth)
    out = {"truth_hours": int(np.isfinite(truth).sum()), "estimated_share": round(float(ok.sum() / max(np.isfinite(truth).sum(), 1)), 3),
           "mae_f": round(float(np.abs(t[ok] - truth[ok]).mean()), 3), "bias_f": round(float((t[ok] - truth[ok]).mean()), 3),
           "p10_p90_coverage": round(float(((truth >= lo) & (truth <= hi))[ok].mean()), 3),
           "mean_width_f": round(float((hi - lo)[ok].mean()), 2)}
    if base is not None:
        both = ok & np.isfinite(base)
        out["common_hours"] = int(both.sum())
        out["mae_common_f"] = round(float(np.abs(t[both] - truth[both]).mean()), 3)
        out["peer_median_mae_common_f"] = round(float(np.abs(base[both] - truth[both]).mean()), 3)
    return out


res: dict = {"terra_held_out": {}, "asos_sites": {}}
for name, (a, b) in {"oct_mar_2024_25": (T("2024-10-01T08:00Z"), T("2025-04-01T08:00Z")),
                     "apr_aug_2025": (T("2025-04-01T08:00Z"), T("2025-09-01T08:00Z")),
                     "mar_2026": (T("2026-03-01T08:00Z"), T("2026-04-01T08:00Z"))}.items():
    hours = pd.date_range(a, b - H1, freq="h")
    obs = obs_of(ctx.store.readings(a, b - H1, ET))
    usable = obs[(obs["quality"] == "usable") & (obs["source_flag"] == "")]
    wide = usable.pivot(index="timestamp", columns="station_id", values="temperature_f").reindex(hours)
    per = {}
    for sid in ET:
        s = geo.set_index("station_id").loc[sid]
        est = estimate_location(float(s.lat), float(s.lon), float(s.elevation_ft), hours, b - H1, obs, geo,
                                exclude_station_ids={sid}, location_id=f"HELD-OUT-{sid}")
        peers = wide[[c for c in ET if c != sid and c in wide]]
        med = peers.median(axis=1).where(peers.notna().sum(axis=1) >= 3).to_numpy()
        truth = wide[sid].to_numpy() if sid in wide else np.full(len(hours), np.nan)
        per[str(sid)] = score(est, truth, med)
    pooled = {k: round(float(np.mean([v[k] for v in per.values()])), 3)
              for k in ("mae_f", "p10_p90_coverage", "mae_common_f", "peer_median_mae_common_f", "estimated_share")}
    res["terra_held_out"][name] = {"pooled_mean_of_stations": pooled, "per_station": per}

asos = SqlStore(os.environ["CHILLWATCH_ASOS_DB_URL"])
ast = asos.stations()
for name, (a, b) in {"winter_2025_26": (T("2025-12-01T08:00Z"), T("2026-03-01T08:00Z")),
                     "mar_2026": (T("2026-03-01T08:00Z"), T("2026-04-01T08:00Z"))}.items():
    hours = pd.date_range(a, b - H1, freq="h")
    obs = obs_of(ctx.store.readings(a, b - H1, ET))
    ra = asos.readings(a, b - H1)
    per = {}
    for site in ["EDU", "DWA", "SMF", "SAC", "VCB", "SUU"]:
        s = ast[ast["source_station_id"] == site].iloc[0]
        est = estimate_location(float(s.lat), float(s.lon), float(s.elevation_ft), hours, b - H1, obs, geo,
                                location_id=f"TEST-{site}")
        r = ra[(ra["station_id"] == s.station_id) & ra["qc_base"].eq("ok")].set_index("ts")["temp_f"]
        per[site] = score(est, r.reindex(hours).to_numpy())
    res["asos_sites"][name] = {"pooled_mean_of_sites": {k: round(float(np.mean([v[k] for v in per.values()])), 3)
                                                        for k in ("mae_f", "p10_p90_coverage", "estimated_share")},
                               "per_site": per}
print(json.dumps(res, indent=1))
