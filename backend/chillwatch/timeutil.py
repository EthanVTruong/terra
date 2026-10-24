"""Small time helpers."""

from __future__ import annotations

from typing import Any

import pandas as pd

from . import config


def utc(x: Any) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _season_start_mmdd() -> str:
    return str(config.region().get("seasons", {}).get("start_mmdd", "10-01"))


def season_of(ts: pd.Timestamp) -> str:
    """Season label (``2023-24``) for a timestamp: the season starting on or before its PST date."""
    local = utc(ts).tz_localize(None) - pd.Timedelta(hours=8, seconds=1)
    y = local.year if local.strftime("%m-%d") >= _season_start_mmdd() else local.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def season_bounds(season: str, start_mmdd: str = "10-01", end_mmdd: str = "03-31") -> tuple[pd.Timestamp, pd.Timestamp]:
    """Hour-ending UTC bounds of a season (first hour .. last hour inclusive)."""
    y0 = int(season[:4])
    first_day = pd.Timestamp(f"{y0}-{start_mmdd}")
    last_day = pd.Timestamp(f"{y0 + 1}-{end_mmdd}")
    start = (first_day + pd.Timedelta(hours=1 + 8)).tz_localize("UTC")
    end = (last_day + pd.Timedelta(hours=24 + 8)).tz_localize("UTC")
    return start, end


def seasons_between(first: str, last: str) -> list[str]:
    a, b = int(first[:4]), int(last[:4])
    return [f"{y}-{str(y + 1)[2:]}" for y in range(a, b + 1)]


def season_labels(ts: pd.Series | pd.DatetimeIndex) -> pd.Series:
    t = pd.DatetimeIndex(ts).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=8, seconds=1)
    y = pd.Series(t.year.where(t.strftime("%m-%d") >= _season_start_mmdd(), t.year - 1),
                  index=getattr(ts, "index", None))
    return y.astype(str) + "-" + (y + 1).astype(str).str[2:]


def focus_bounds(season: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The Oct-Mar part of a season that the benchmark and the chill map work on."""
    s = config.region().get("seasons", {})
    return season_bounds(season, str(s.get("focus_start_mmdd", s.get("start_mmdd", "10-01"))),
                         str(s.get("focus_end_mmdd", s.get("end_mmdd", "03-31"))))
