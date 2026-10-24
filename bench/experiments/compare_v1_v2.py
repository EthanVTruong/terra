"""cimis-v1 (Oct-Mar training) vs cimis-v2 (year-round training) on the held-out 2023-24 / 2024-25.

Known-station accuracy (target excluded from its own features), raw and with hour-of-day offsets
learned from 2022-23 residuals of the same model (a training season for both models), split into
the Oct-Mar focus window and Apr-Sep. Offsets either pooled over all 12 months of 2022-23 or
matched to the half-year (Oct-Mar offsets for Oct-Mar hours, Apr-Sep offsets for Apr-Sep hours);
the pooled table mixes cimis-v1's out-of-sample summer residuals into its winter offsets, so the
matched one is the fair comparison. Chill-hour error per station-season from Nov 1 to Mar 31.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from chillwatch import config
from chillwatch.context import get_context
from chillwatch.normalize import to_pst
from chillwatch.qc.model import ModelBundle, _season_frame, chill_mask, slim_readings
from chillwatch.timeutil import season_bounds

ctx = get_context()
reg = config.region()
st = ctx.region_stations()
models = {v: ModelBundle.load(config.data_dir() / "models" / v) for v in sys.argv[1:] or ["cimis-v1", "cimis-v2"]}
extras = ("rh_pct", "dewpoint_f", "solar_wm2", "wind_mph")
K = float(config.thresholds()["offsets"].get("hour_shrink_k", 14))


def frame(season):
    a, b = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    r = slim_readings(ctx.store.readings(a, b, st["station_id"].tolist()))
    _, tf, _ = _season_frame((season, r, st, a, b, extras))
    pst = to_pst(pd.DatetimeIndex(tf["ts"]))
    tf["hour"] = pst.hour
    tf["focus"] = ((pst - pd.Timedelta(seconds=1)).month.isin([10, 11, 12, 1, 2, 3]))
    tf["season"] = season
    return tf


def hourly_table(src):
    nd = src.groupby(["station_id", "night"])["resid"].median()
    hr = src.groupby(["station_id", "hour"])["resid"].agg(["median", "size"])
    night = np.array([(h >= 19) or (h <= 6) for h in hr.index.get_level_values(1)])
    base = nd.reindex(pd.MultiIndex.from_arrays([hr.index.get_level_values(0), night])).to_numpy()
    w = hr["size"].to_numpy() / (hr["size"].to_numpy() + K)
    return pd.Series(w * hr["median"].to_numpy() + (1 - w) * np.nan_to_num(base), index=hr.index)


src = frame("2022-23")
test = pd.concat([frame("2023-24"), frame("2024-25")], ignore_index=True)
out = {}
for v, bm in models.items():
    feats = bm.features
    for df in (src, test):
        for f in feats:
            if f not in df:
                df[f] = np.nan
    src["resid"] = src["y"] - bm.booster.predict(src[feats].to_numpy(dtype="float32"))
    pred = bm.booster.predict(test[feats].to_numpy(dtype="float32"))
    key = pd.MultiIndex.from_frame(test[["station_id", "hour"]])
    off = hourly_table(src).reindex(key).fillna(0).to_numpy()
    focus = test["focus"].to_numpy()
    off_m = np.where(focus, hourly_table(src[src["focus"]]).reindex(key).fillna(0).to_numpy(),
                     hourly_table(src[~src["focus"]]).reindex(key).fillna(0).to_numpy())
    res = {}
    for name, p in (("model", pred), ("model_plus_hourly_offset", pred + off),
                    ("model_plus_matched_hourly_offset", pred + off_m)):
        e = p - test["y"].to_numpy()
        part = {}
        for w, m in (("oct_mar", test["focus"].to_numpy()), ("apr_sep", ~test["focus"].to_numpy())):
            part[w] = {"n": int(m.sum()), "mae_f": round(float(np.abs(e[m]).mean()), 4) if m.any() else None}
        cm = chill_mask(test["ts"])
        d = test[cm].assign(p=p[cm])
        inr = lambda x: (x >= 32) & (x <= 45)  # noqa: E731
        g = d.assign(o=inr(d["y"]), q=inr(d["p"])).groupby(["season", "station_id"])[["o", "q"]].sum()
        part["chill_net_abs_mean_h"] = round(float((g["q"] - g["o"]).abs().mean()), 1)
        part["chill_net_abs_median_h"] = float((g["q"] - g["o"]).abs().median())
        res[name] = part
    out[v] = res
print(json.dumps(out, indent=1))
