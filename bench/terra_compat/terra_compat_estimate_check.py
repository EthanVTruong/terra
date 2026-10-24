"""Check that chillwatch.estimate.estimate_location reproduces the evaluated orchard-path method.

Runs estimate_location at the six ASOS test sites from Terra's five stations only and compares
with terra_compat_extra.py's "phys_et5_shrink0.0" column (IDW, lapse off) on the same hours.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

from chillwatch.context import get_context
from chillwatch.estimate import estimate_location
from chillwatch.store import SqlStore

ET = [6, 226, 139, 212, 235]


def observations_from_readings(readings: pd.DataFrame) -> pd.DataFrame:
    """ChillWatch readings in the observation shape estimate_location expects (Terra supplies its own)."""
    return pd.DataFrame({
        "station_id": readings["station_id"].astype(int).to_numpy(),
        "timestamp": pd.to_datetime(readings["ts"], utc=True),
        "temperature_f": readings["temp_f"].to_numpy(),
        "quality": np.where(readings["qc_base"].eq("ok"), "usable", readings["qc_base"].astype(str)),
        "source_flag": readings["qc_source"].fillna("").astype(str).str.strip().to_numpy(),
    })
T = pd.Timestamp
H1 = pd.Timedelta(hours=1)
WINDOWS = {"untouched_2026-03": (T("2026-03-01T08:00Z"), T("2026-04-01T08:00Z")),
           "inspected_winter": (T("2025-12-01T08:00Z"), T("2026-03-01T08:00Z"))}
ex = pd.read_parquet(sys.argv[1])
ctx = get_context()
st = ctx.region_stations()
st = st[st["station_id"].isin(ET)][["station_id", "lat", "lon", "elevation_ft"]]
asos = SqlStore(os.environ["CHILLWATCH_ASOS_DB_URL"])
ast = asos.stations()
out = {}
for w, (a, b) in WINDOWS.items():
    hours = pd.date_range(a, b - H1, freq="h")
    obs = observations_from_readings(ctx.store.readings(a, b - H1, ET))
    res = {}
    for site in ["EDU", "DWA", "SMF", "SAC", "VCB", "SUU"]:
        s = ast[ast["source_station_id"] == site].iloc[0]
        e = estimate_location(float(s.lat), float(s.lon), float(s.elevation_ft), hours, b - H1, obs, st,
                              location_id=f"TEST-{site}", geometry_source="IEM station metadata")
        ref = ex[(ex["part"] == "orchard") & (ex["window"] == w) & (ex["site"] == site)].set_index("ts")
        r = ref["phys_et5_shrink0.0"].reindex(e["valid_utc"]).to_numpy()
        n3 = ref["phys_et5_n"].reindex(e["valid_utc"]).to_numpy() >= 3
        t = e["temperature"].to_numpy()
        both = np.isfinite(t) & np.isfinite(r)
        truth = ref["obs_cw"].reindex(e["valid_utc"]).to_numpy()
        ok = np.isfinite(t) & np.isfinite(truth)
        res[site] = {"hours": len(e), "ok": int((e["status"] == "ok").sum()),
                     "same_hours_as_3_peer_rule": bool(np.array_equal(np.isfinite(t), np.isfinite(r) & n3)),
                     "max_abs_diff_vs_evaluated": float(np.nanmax(np.abs(t[both] - r[both]))),
                     "methods": e["method"].value_counts().to_dict(),
                     "mae_vs_asos": round(float(np.abs(t[ok] - truth[ok]).mean()), 3)}
    out[w] = res
print(json.dumps(out, indent=1))
