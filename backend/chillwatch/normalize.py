"""Normalize source data into the canonical hourly schema.

Conventions:
* ``ts`` is UTC, tz-aware and *hour-ending* (the value at 10:00 covers 09:00-10:00).
* CIMIS reports Pacific Standard Time all year (no daylight saving), hour-ending
  ``0100``..``2400``; ``2400`` is midnight ending that date.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from . import config

PST = timezone(timedelta(hours=-8), "PST")
QC_ORDER = {"ok": 0, "suspect": 1, "invalid": 2, "missing": 3}
_OFFSET_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


# --------------------------------------------------------------------------- time
def cimis_ts_utc(dates: Iterable[str] | pd.Series, hours: Iterable[str] | pd.Series) -> pd.Series:
    """CIMIS (Date, Hour) in PST hour-ending -> UTC hour-ending timestamps.

    ``ts = Date 00:00 PST + Hour``; ``2400`` therefore rolls to the next day 00:00 PST.
    """
    d = pd.to_datetime(pd.Series(list(dates)), format="%Y-%m-%d")
    h = pd.Series(list(hours)).astype(str).str.strip().str.zfill(4)
    hh = h.str[:2].astype(int)
    mm = h.str[2:].astype(int)
    local = d + pd.to_timedelta(hh, unit="h") + pd.to_timedelta(mm, unit="m")
    return (local + pd.Timedelta(hours=8)).dt.tz_localize("UTC")


def pst_day_hours(start: date, end: date) -> pd.DatetimeIndex:
    """All hour-ending UTC timestamps covering PST dates start..end inclusive."""
    first = pd.Timestamp(datetime.combine(start, datetime.min.time())) + pd.Timedelta(hours=1 + 8)
    last = pd.Timestamp(datetime.combine(end, datetime.min.time())) + pd.Timedelta(hours=24 + 8)
    return pd.date_range(first.tz_localize("UTC"), last.tz_localize("UTC"), freq="h")


def to_pst(ts: pd.Series | pd.DatetimeIndex) -> pd.Series | pd.DatetimeIndex:
    """UTC timestamps -> naive PST wall-clock (fixed UTC-8)."""
    if isinstance(ts, pd.DatetimeIndex):
        return ts.tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=8)
    return ts.dt.tz_convert("UTC").dt.tz_localize(None) - pd.Timedelta(hours=8)


def local_date_of_hour(ts: pd.Series) -> pd.Series:
    """PST calendar date an hour-ending timestamp belongs to (2400 belongs to the prior day)."""
    return (to_pst(ts) - pd.Timedelta(hours=1)).dt.date


def parse_local_timestamps(values: Iterable[Any], tz: str | None) -> pd.Series:
    """Parse ISO-8601 strings to UTC.

    Strings with an explicit offset are honoured. Strings without one are localized to
    the station's IANA zone ``tz`` (daylight saving aware; ambiguous / non-existent
    local times become NaT). Without an offset and without ``tz`` the row is rejected (NaT).
    """
    s = pd.Series(list(values), dtype=object).astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")
    has_off = s.str.contains(_OFFSET_RE)
    if has_off.any():
        out[has_off] = pd.to_datetime(s[has_off], format="ISO8601", utc=True, errors="coerce")
    naive = ~has_off & s.ne("") & s.ne("nan") & s.ne("None")
    if naive.any() and tz:
        local = pd.to_datetime(s[naive], format="ISO8601", errors="coerce")
        loc = pd.DatetimeIndex(local).tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        out[naive] = loc.tz_convert("UTC")
    return out


# --------------------------------------------------------------------------- units
def f_from_c(c: Any) -> Any:
    return np.asarray(c, dtype="float64") * 9.0 / 5.0 + 32.0


def to_fahrenheit(values: pd.Series, units: pd.Series | str) -> pd.Series:
    v = pd.to_numeric(values, errors="coerce").astype("float64")
    u = units if isinstance(units, pd.Series) else pd.Series(units, index=v.index)
    u = u.astype(str).str.strip().str.upper().str.replace("°", "", regex=False)
    is_c = u.isin(["C", "DEGC", "CELSIUS"])
    is_f = u.isin(["F", "DEGF", "FAHRENHEIT"])
    out = v.where(~is_c, v * 9.0 / 5.0 + 32.0)
    return out.where(is_c | is_f)


# --------------------------------------------------------------------------- resample
def to_hourly(
    df: pd.DataFrame,
    value_cols: list[str],
    keys: list[str] | None = None,
    min_coverage: float | None = None,
) -> pd.DataFrame:
    """Sub-hourly -> hour-ending hourly means; hours below ``min_coverage`` become NaN.

    Coverage = non-null samples in the hour / samples expected from the station's
    median sampling interval.
    """
    keys = keys or ["source", "source_station_id"]
    if min_coverage is None:
        min_coverage = float(config.thresholds()["normalize"]["min_coverage"])
    out = []
    for key, g in df.dropna(subset=["ts"]).sort_values("ts").groupby(keys, sort=False):
        g = g.drop_duplicates("ts", keep="last")
        step_min = g["ts"].diff().dt.total_seconds().div(60).median()
        if not np.isfinite(step_min) or step_min <= 0:
            step_min = 60.0
        expected = max(1.0, round(60.0 / min(step_min, 60.0)))
        hour = g["ts"].dt.ceil("h")
        agg = g.assign(_h=hour).groupby("_h")[value_cols]
        means = agg.mean()
        counts = agg.count()
        means = means.where(counts.div(expected) >= min_coverage - 1e-9)
        means.index.name = "ts"
        means = means.reset_index()
        for k, v in zip(keys, key if isinstance(key, tuple) else (key,), strict=True):
            means[k] = v
        out.append(means)
    if not out:
        return pd.DataFrame(columns=[*keys, "ts", *value_cols])
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------- QC mapping
def qc_status_from_source(
    qc_source: pd.Series, temp_f: pd.Series, flags: dict[str, Any] | None = None
) -> pd.Series:
    """Map source QC codes to ``ok | suspect | invalid | missing``."""
    flags = flags or config.cimis_flags()
    severe = set((flags.get("severe") or {}).keys())
    info = set((flags.get("informative") or {}).keys())

    def one(code: Any) -> str:
        if code is None or (isinstance(code, float) and np.isnan(code)):
            return "ok"
        chars = [c for c in str(code).strip().upper() if not c.isspace()]
        if not chars:
            return "ok" if flags.get("blank_is_ok", True) else "suspect"
        worst = "ok"
        for c in chars:
            st = "invalid" if c in severe else "ok" if c in info else "suspect"
            if QC_ORDER[st] > QC_ORDER[worst]:
                worst = st
        return worst

    codes = qc_source.astype(object)
    uniq = {c: one(c) for c in pd.unique(codes.where(codes.notna(), None))}
    status = codes.where(codes.notna(), None).map(lambda c: uniq[c]).astype(object)
    status[pd.to_numeric(temp_f, errors="coerce").isna()] = "missing"
    return status


def worst_status(a: pd.Series, b: pd.Series) -> pd.Series:
    ra = a.map(QC_ORDER).fillna(0)
    rb = b.map(QC_ORDER).fillna(0)
    return a.where(ra >= rb, b)


# --------------------------------------------------------------------------- dedupe / gaps
def dedupe(df: pd.DataFrame, keys: list[str] | None = None, order_col: str = "ingested_at") -> pd.DataFrame:
    """Keep the latest-ingested row per key."""
    keys = keys or ["source", "source_station_id", "ts"]
    if order_col in df.columns:
        df = df.sort_values(order_col, kind="stable")
    return df.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)


def complete_hours(
    df: pd.DataFrame,
    hours: pd.DatetimeIndex,
    keys: list[str] | None = None,
    station_ids: Iterable[Any] | None = None,
) -> pd.DataFrame:
    """Insert explicit rows (temp_f NaN, qc_status ``missing``) for expected hours with no data."""
    keys = keys or ["source", "source_station_id"]
    ids = df[keys].drop_duplicates()
    if station_ids is not None:
        extra = pd.DataFrame(list(station_ids), columns=keys)
        ids = pd.concat([ids, extra]).drop_duplicates()
    if ids.empty:
        return df
    grid = ids.merge(pd.DataFrame({"ts": hours}), how="cross")
    out = grid.merge(df, on=[*keys, "ts"], how="left")
    if "qc_status" in out.columns:
        out["qc_status"] = out["qc_status"].astype(object).where(out["qc_status"].notna(), "missing")
    else:
        out["qc_status"] = np.where(out["temp_f"].isna(), "missing", "ok")
    out.loc[out["temp_f"].isna(), "qc_status"] = "missing"
    if "qc_base" in out.columns:
        out["qc_base"] = out["qc_base"].astype(object).where(out["qc_base"].notna(), "missing")
    return out


def finalize_hourly(
    df: pd.DataFrame,
    station_map: dict[tuple[str, str], int],
    ingested_at: datetime | None = None,
    flags: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Canonical adapter output -> store rows (adds station_id, qc_base, qc_status, ingested_at)."""
    out = df.copy()
    keys = list(zip(out["source"].astype(str), out["source_station_id"].astype(str), strict=True))
    out["station_id"] = pd.array([station_map.get(k) for k in keys], dtype="Int64")
    out = out[out["station_id"].notna()].copy()
    out["station_id"] = out["station_id"].astype("int64")
    if "qc_base" not in out.columns:
        out["qc_base"] = qc_status_from_source(out["qc_source"], out["temp_f"], flags)
    out["qc_status"] = out["qc_base"]
    ts = pd.Timestamp(ingested_at) if ingested_at is not None else pd.Timestamp.now(tz="UTC")
    out["ingested_at"] = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return out

