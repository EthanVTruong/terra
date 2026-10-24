"""Demo scenario replay (season 2025-26): inject the four faults and run the daily job day by day."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .. import config
from ..context import Context
from ..pipeline import replay
from ..store import MemoryStore
from ..timeutil import focus_bounds, season_bounds
from .faults import apply_scenario


def run_scenario(path: str | Path, ctx: Context | None = None, days: int | None = None,
                 echo: Callable[[str], None] = print) -> dict[str, Any]:
    from ..bench.run import match_faults
    from ..context import get_context

    ctx = ctx or get_context()
    spec = yaml.safe_load(Path(path).read_text())
    season = spec.get("season") or config.region()["demo_season"]
    a, b = focus_bounds(season)  # the Oct-Mar part of the season the scenario is written for
    if days:
        b = min(b, a + pd.Timedelta(days=days) - pd.Timedelta(hours=1))
    stations = ctx.region_stations()
    truth = ctx.store.readings(a, b, stations["station_id"].tolist())
    faulty, labels = apply_scenario(path, truth, stations)
    for lab in labels:
        echo(f"fault: station {lab.station_id} {lab.type} from {lab.start_ts} {lab.params}")
    pre = Context(MemoryStore(stations, faulty), ctx.model)
    pre.precompute_expected(a, b)
    rc = Context(MemoryStore(stations), ctx.model)
    rc.freeze_from(pre)
    seen: set[tuple[int, str, str]] = set()
    log: list[dict[str, Any]] = []

    def on_day(as_of: pd.Timestamp, dets: list) -> None:
        for d in dets:
            if d.key() in seen:
                continue
            seen.add(d.key())
            log.append({"run": as_of.isoformat(), **d.to_dict()})
            echo(f"[run {as_of:%Y-%m-%d %H:%M}Z] station {d.station_id:>4} {d.type:<12} {d.severity:<8} "
                 f"start {pd.Timestamp(d.start_ts):%Y-%m-%d %H:%M}Z  {d.evidence}")

    first_day = (a - pd.Timedelta(hours=9)).normalize().tz_localize(None)
    last_day = (b - pd.Timedelta(hours=9)).normalize().tz_localize(None)
    res = replay(rc, faulty, first_day, last_day, on_day=on_day)
    matches = match_faults(labels, res.runs)
    echo("")
    echo("scenario faults:")
    for m in matches:
        if m["detected"]:
            runs_after = int(m["hours_to_detect"] // 24) + 1
            echo(f"  {m['type']:<8} station {m['station_id']:>4}: detected as {m['detected_as']} "
                 f"{m['hours_to_detect']:.0f} h after onset (daily run #{runs_after} after onset)")
        else:
            echo(f"  {m['type']:<8} station {m['station_id']:>4}: NOT detected")
    return {"season": season, "faults": [lab.to_dict() for lab in labels], "matches": matches, "detections": log,
            "repaired": rc.store.readings(a, b)}


def synthetic_dry_run(n_stations: int = 30, echo: Callable[[str], None] = print,
                      out_dir: Path | None = None, days: int = 80) -> dict[str, Any]:
    """Train on synthetic seasons and replay the demo scenario. SYNTHETIC: never present these numbers."""
    import os

    from ..qc.model import train
    from .synthetic import make_readings, make_stations

    out_dir = out_dir or config.data_dir() / "demo" / "synthetic"
    os.environ["CHILLWATCH_DATA_DIR"] = str(out_dir)
    st = make_stations(n_stations)
    seasons = config.region()["seasons"]
    frames = []
    for year in (2021, 2022, 2025):
        season = f"{year}-{str(year + 1)[2:]}"
        start, end = season_bounds(season, seasons["start_mmdd"], seasons["end_mmdd"])
        frames.append(make_readings(st, start.tz_localize(None), end.tz_localize(None), seed=year))
    store = MemoryStore(st, pd.concat(frames, ignore_index=True))
    echo("SYNTHETIC DATA - pipeline dry run only")
    bundle = train(store, seasons=["2021-22", "2022-23"], version="synthetic", loso_folds=3,
                   out_dir=out_dir / "models")
    echo(f"synthetic LOSO MAE {bundle.metrics['loso_model']['mae_f']:.2f} F")
    ctx = Context(store, bundle)
    path = config.config_dir() / "scenarios" / "demo_2025_26.yaml"
    return run_scenario(path, ctx, days=days, echo=echo)
