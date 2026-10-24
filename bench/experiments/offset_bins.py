"""Known-station offsets: night/day (2 bins, current) vs hour-of-day (24 bins) vs month x hour.

Model cimis-v1 predictions at every station (target excluded from its features) for 2022-23
(training season, offset source as in the holdout metric) and the held-out 2023-24 / 2024-25.
A: offsets from all of 2022-23, applied to the held-out seasons (the holdout metric's setup).
B: production setup, offsets re-learned weekly from the previous 28 days of the same season.
Scored from Nov 1 (chill season): MAE, and per station-season chill-hour net and gross error.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from chillwatch import config
from chillwatch.context import get_context
from chillwatch.qc.model import _season_frame, chill_mask, slim_readings
from chillwatch.timeutil import season_bounds

SHRINK_K = 14  # hour bin blended toward its night/day median with weight n / (n + K)
ctx = get_context()
reg = config.region()
bundle = ctx.model
st = ctx.region_stations()
feats = bundle.features
extras = ("rh_pct", "dewpoint_f", "solar_wm2", "wind_mph")
frames = {}
for s in ["2022-23", "2023-24", "2024-25"]:
    a, b = season_bounds(s, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    r = slim_readings(ctx.store.readings(a, b, st["station_id"].tolist()))
    _, tf, _ = _season_frame((s, r, st, a, b, extras))
    for f in feats:
        if f not in tf:
            tf[f] = np.nan
    tf["pred"] = bundle.booster.predict(tf[feats].to_numpy(dtype="float32"))
    pst = pd.DatetimeIndex(tf["ts"]).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=8)
    keep = ["station_id", "ts", "night", "y", "pred"]
    frames[s] = tf[keep].assign(hour=pst.hour, month=(pst - pd.Timedelta(seconds=1)).month, resid=tf["y"] - tf["pred"])
    print(f"{s}: {len(tf)} rows", file=sys.stderr, flush=True)
    del tf, r


def chill_scores(df, col):
    m = chill_mask(df["ts"])
    d = df[m]
    inr = lambda x: (x >= 32) & (x <= 45)  # noqa: E731
    o, e = inr(d["y"]), inr(d[col])
    g = pd.DataFrame({"st": d["station_id"], "fp": e & ~o, "fn": ~e & o}).groupby("st")[["fp", "fn"]].sum()
    return {"mae_from_nov1": round(float((d[col] - d["y"]).abs().mean()), 4),
            "chill_net_abs_median": float((g["fp"] - g["fn"]).abs().median()),
            "chill_net_abs_mean": round(float((g["fp"] - g["fn"]).abs().mean()), 2),
            "chill_gross_mean": round(float((g["fp"] + g["fn"]).mean()), 2)}


def apply(df, table, keys):
    idx = pd.MultiIndex.from_frame(df[keys])
    return df["pred"].to_numpy() + table.reindex(idx).fillna(0).to_numpy()


def blended(src):
    nd = src.groupby(["station_id", "night"])["resid"].median()
    hr = src.groupby(["station_id", "hour"])["resid"].agg(["median", "size"])
    night_of_hour = src.groupby(["station_id", "hour"])["night"].agg(lambda x: x.mode().iloc[0])
    base = nd.reindex(pd.MultiIndex.from_arrays([hr.index.get_level_values(0), night_of_hour.to_numpy()])).to_numpy()
    w = hr["size"] / (hr["size"] + SHRINK_K)
    return pd.Series(w.to_numpy() * hr["median"].to_numpy() + (1 - w.to_numpy()) * base, index=hr.index)


out = {"A_previous_season": {}, "B_rolling_28d_weekly": {}}
src = frames["2022-23"]
tables = {"night_day": (src.groupby(["station_id", "night"])["resid"].median(), ["station_id", "night"]),
          "hour_of_day": (src.groupby(["station_id", "hour"])["resid"].median(), ["station_id", "hour"]),
          "hour_blended": (blended(src), ["station_id", "hour"]),
          "month_x_hour": (src.groupby(["station_id", "month", "hour"])["resid"].median(), ["station_id", "month", "hour"])}
test = pd.concat([frames["2023-24"], frames["2024-25"]], ignore_index=True)
test = test[test["station_id"].isin(src["station_id"].unique())].reset_index(drop=True)
test["none"] = test["pred"]
res = {"none": chill_scores(test, "none")}
for name, (tab, keys) in tables.items():
    test[name] = apply(test, tab, keys)
    res[name] = chill_scores(test, name)
out["A_previous_season"] = res

# B: weekly re-learned offsets from the previous 28 days (same season), first offsets on day 28
rows = []
for s in ["2023-24", "2024-25"]:
    f = frames[s]
    a, _ = season_bounds(s, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    t = a + pd.Timedelta(days=28)
    while t < f["ts"].max():
        win = f[(f["ts"] >= t - pd.Timedelta(days=28)) & (f["ts"] < t)]
        nxt = f[(f["ts"] >= t) & (f["ts"] < t + pd.Timedelta(days=7))].copy()
        ok = win.groupby("station_id").size()
        win = win[win["station_id"].isin(ok[ok >= 24 * 7].index)]
        nxt["none"] = nxt["pred"]
        nxt["night_day"] = apply(nxt, win.groupby(["station_id", "night"])["resid"].median(), ["station_id", "night"])
        nxt["hour_of_day"] = apply(nxt, win.groupby(["station_id", "hour"])["resid"].median(), ["station_id", "hour"])
        nxt["hour_blended"] = apply(nxt, blended(win), ["station_id", "hour"])
        rows.append(nxt)
        t += pd.Timedelta(days=7)
rb = pd.concat(rows, ignore_index=True)
out["B_rolling_28d_weekly"] = {c: chill_scores(rb, c) for c in ["none", "night_day", "hour_of_day", "hour_blended"]}
out["n_test_rows_A"] = int(len(test))
out["n_test_rows_B"] = int(len(rb))
print(json.dumps(out, indent=1))
