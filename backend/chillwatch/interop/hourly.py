"""Validated samples -> canonical hourly rows (``sources.base.HOURLY_COLUMNS`` + extras).

Conventions match the rest of ChillWatch (``normalize.py``): ``ts`` is UTC and hour-ending, so a
sample received at 09:15 UTC belongs to the hour ending 10:00 UTC.

Hour status:
* ``ok``      - enough ``ok`` samples (``min_hour_coverage`` of the expected count).
* ``suspect`` - some ``ok`` samples but low coverage, or only suspect samples (mean of those).
* ``invalid`` - samples arrived but every one was unusable.
* ``missing`` - nothing arrived for the hour.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..sources.base import HOURLY_COLUMNS
from .contract import Issue, Node, c_to_f
from .rules import logger_thresholds

HOURLY_LOGGER_COLUMNS = [*HOURLY_COLUMNS, "rh_pct", "n_samples", "n_ok", "coverage", "issues"]


def hour_grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start).ceil("h"), pd.Timestamp(end).ceil("h"), freq="h", tz="UTC")


def samples_to_hourly(validated: pd.DataFrame, node: Node, start: Any = None, end: Any = None,
                      th: dict[str, Any] | None = None) -> pd.DataFrame:
    th = th or logger_thresholds()
    df = validated.copy()
    if df.empty and (start is None or end is None):
        return pd.DataFrame(columns=HOURLY_LOGGER_COLUMNS)
    df["ts"] = df["received_at"].dt.ceil("h")
    lo = pd.Timestamp(start, tz="UTC") if start is not None and pd.Timestamp(start).tzinfo is None else start
    hi = pd.Timestamp(end, tz="UTC") if end is not None and pd.Timestamp(end).tzinfo is None else end
    grid = hour_grid(lo if lo is not None else df["ts"].min(), hi if hi is not None else df["ts"].max())
    expected = max(1.0, 3600.0 / float(node.sample_interval_s))
    min_cov = float(th["min_hour_coverage"])

    rows = []
    groups = dict(tuple(df.groupby("ts"))) if len(df) else {}
    for ts in grid:
        g = groups.get(ts)
        if g is None or g.empty:
            rows.append({"ts": ts, "temp_f": np.nan, "qc_source": "missing", "n_samples": 0, "n_ok": 0,
                         "coverage": 0.0, "rh_pct": np.nan, "issues": str(Issue.NO_SAMPLES)})
            continue
        ok = g[g["status"] == "ok"]
        sus = g[g["status"] == "suspect"]
        cov = min(1.0, len(ok) / expected)
        found = sorted({i for s in g["issues"] for i in str(s).split(",") if i})
        if len(ok) and cov >= min_cov:
            status, use = "ok", ok
        elif len(ok):
            status, use = "suspect", ok
            found = sorted({*found, str(Issue.LOW_COVERAGE)})
        elif len(sus):
            status, use = "suspect", sus
        else:
            status, use = "invalid", g.iloc[0:0]
        rows.append({
            "ts": ts,
            "temp_f": float(c_to_f(use["temp_c"].mean())) if len(use) else np.nan,
            "qc_source": status,
            "n_samples": int(len(g)),
            "n_ok": int(len(ok)),
            "coverage": round(cov, 3),
            "rh_pct": float(use["rh_pct"].mean()) if len(use) and use["rh_pct"].notna().any() else np.nan,
            "issues": ",".join(found),
        })
    out = pd.DataFrame(rows)
    out["source"] = "logger"
    out["source_station_id"] = str(node.node_id)
    out["raw_ref"] = None
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    return out[HOURLY_LOGGER_COLUMNS]


def gaps(hourly: pd.DataFrame, statuses: tuple[str, ...] = ("missing", "invalid")) -> list[dict[str, Any]]:
    """Consecutive runs of unusable hours: ``[{"start", "end", "hours", "status"}]`` (hour-ending UTC)."""
    bad = hourly["qc_source"].isin(statuses).to_numpy()
    out, i, n = [], 0, len(hourly)
    while i < n:
        if not bad[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and bad[j + 1]:
            j += 1
        kinds = sorted(set(hourly["qc_source"].iloc[i:j + 1]))
        out.append({"start": hourly["ts"].iloc[i].isoformat(), "end": hourly["ts"].iloc[j].isoformat(),
                    "hours": int(j - i + 1), "status": "/".join(kinds)})
        i = j + 1
    return out
