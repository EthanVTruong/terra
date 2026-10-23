"""Daily-job glue: ingest -> run_detection -> fill. Shared by the scheduler, demo and bench."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from . import config
from .context import Context
from .qc.detect import Detection, run_detection
from .qc.model import update_learned_offsets
from .repair.fill import fill_store
from .timeutil import utc

log = logging.getLogger(__name__)


def pst_day_end(day: Any) -> pd.Timestamp:
    """Last hour-ending timestamp (24:00 PST) of a PST calendar day, in UTC."""
    d = pd.Timestamp(day).tz_localize(None).normalize() if pd.Timestamp(day).tzinfo else pd.Timestamp(day).normalize()
    return (d + pd.Timedelta(hours=24 + 8)).tz_localize("UTC")


def ingest_from_dataframe(ctx: Context, df: pd.DataFrame, start: Any, end: Any) -> int:
    """Upsert the rows of ``df`` with ``start <= ts <= end`` (one day's delivery in a replay)."""
    s, e = utc(start), utc(end)
    part = df[(df["ts"] >= s) & (df["ts"] <= e)]
    n = ctx.store.upsert_readings(part)
    ctx.invalidate()
    return n


def daily_run(ctx: Context, as_of: Any, lookback_days: int = 7) -> tuple[list[Detection], list[dict[str, Any]]]:
    dets = run_detection(as_of, lookback_days=lookback_days, ctx=ctx)
    a = utc(as_of)
    gaps = fill_store(a - pd.Timedelta(days=max(lookback_days, 60)), a, ctx=ctx)
    return dets, gaps


@dataclass
class ReplayResult:
    runs: list[tuple[pd.Timestamp, list[Detection]]] = field(default_factory=list)

    def first_seen(self) -> dict[tuple[int, str, str], pd.Timestamp]:
        out: dict[tuple[int, str, str], pd.Timestamp] = {}
        for as_of, dets in self.runs:
            for d in dets:
                out.setdefault(d.key(), as_of)
        return out

    def all_detections(self) -> list[tuple[pd.Timestamp, Detection]]:
        return [(a, d) for a, ds in self.runs for d in ds]


def replay(
    ctx: Context,
    data: pd.DataFrame,
    first_day: Any,
    last_day: Any,
    lookback_days: int = 7,
    learn_offsets: bool = True,
    on_day: Callable[[pd.Timestamp, list[Detection]], None] | None = None,
) -> ReplayResult:
    """Feed ``data`` into ``ctx.store`` one PST day at a time, running the daily job after each."""
    res = ReplayResult()
    rec = int(config.thresholds()["offsets"]["recompute_days"])
    days = pd.date_range(pd.Timestamp(first_day).normalize(), pd.Timestamp(last_day).normalize(), freq="D")
    for i, day in enumerate(days):
        end = pst_day_end(day)
        start = end - pd.Timedelta(hours=23)
        ingest_from_dataframe(ctx, data, start, end)
        if learn_offsets and i > 0 and i % rec == 0:
            update_learned_offsets(end, ctx=ctx)
        dets, _ = daily_run(ctx, end, lookback_days)
        res.runs.append((end, dets))
        if on_day:
            on_day(end, dets)
    return res


def daily_job(as_of: datetime | None = None, ctx: Context | None = None) -> list[Detection]:
    """What the scheduler calls after `chillwatch ingest --days 3`."""
    from .context import get_context

    ctx = ctx or get_context()
    a = utc(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC").floor("h")
    dets, _ = daily_run(ctx, a)
    return dets
