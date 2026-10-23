"""``chillwatch`` command line."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ChillWatch data / QC / ML layer")
stations_app = typer.Typer(no_args_is_help=True, help="Station metadata")
app.add_typer(stations_app, name="stations")
log = logging.getLogger("chillwatch")


def _setup(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _ctx():
    from .context import get_context

    return get_context()


def _source(name: str):
    if name == "asos":
        from .sources.asos import AsosSource

        return AsosSource()
    from .sources.cimis import CimisSource

    return CimisSource()


def _ids(s: str | None) -> list[int] | None:
    return [int(x) for x in s.split(",") if x.strip()] if s else None


def _report(results: dict[int, object]) -> None:
    ok = {k: v for k, v in results.items() if not isinstance(v, Exception)}
    bad = {k: v for k, v in results.items() if isinstance(v, Exception)}
    rows = sum(v for v in ok.values() if isinstance(v, int))
    typer.echo(f"{len(ok)} stations ok, {rows} rows; {len(bad)} failed")
    for k, v in bad.items():
        typer.echo(f"  station {k}: {v}")


@stations_app.command("sync")
def stations_sync(region: Path = typer.Option(None, help="default: $CHILLWATCH_REGION or config/region.yaml"),
                  source: str = typer.Option("cimis", help="cimis | asos"), verbose: bool = False) -> None:
    """Fetch the station list and mark the region's stations."""
    _setup(verbose)
    from .ingest import sync_stations

    st = sync_stations(_source(source), _ctx().store, region)
    typer.echo(f"{len(st)} region stations")
    typer.echo(st[["station_id", "name", "county", "elevation_ft", "is_active", "disconnect_date"]].to_string(index=False))


@app.command()
def backfill(
    from_: str = typer.Option("2005-10-01", "--from"),
    to: str = typer.Option("2026-03-31", "--to"),
    stations: str = typer.Option(None, help="comma-separated station ids"),
    export: bool = typer.Option(True, help="write the Parquet snapshot afterwards"),
    source: str = typer.Option("cimis", help="cimis | asos"),
    verbose: bool = False,
) -> None:
    """Resumable backfill of chill seasons (cached raw responses are never re-downloaded)."""
    _setup(verbose)
    from .ingest import backfill as bf
    from .ingest import export_parquet

    ctx = _ctx()
    _report(bf(_source(source), ctx.store, date.fromisoformat(from_), date.fromisoformat(to), _ids(stations)))
    if export:
        typer.echo(f"parquet: {export_parquet(ctx.store)}")


@app.command()
def ingest(days: int = 3, stations: str = typer.Option(None), source: str = typer.Option("cimis"),
           verbose: bool = False) -> None:
    """Re-fetch the last N days (idempotent upserts; what the daily job calls)."""
    _setup(verbose)
    from .ingest import ingest_recent

    _report(ingest_recent(_source(source), _ctx().store, days, station_ids=_ids(stations)))


@app.command("ingest-diy")
def ingest_diy(registry: Path, uploads: Path, start: str, end: str, verbose: bool = False) -> None:
    """Import grower DIY station uploads (CSV/JSON)."""
    _setup(verbose)
    from .ingest import ingest_station, run_per_station, sync_stations
    from .sources.diy import DiySource

    src = DiySource(registry, uploads)
    ctx = _ctx()
    st = sync_stations(src, ctx.store)
    st = ctx.store.stations()
    st = st[st["source"] == "diy"]
    _report(run_per_station(st, lambda r: ingest_station(src, ctx.store, r, date.fromisoformat(start),
                                                         date.fromisoformat(end))))
    typer.echo(f"rejected rows: {src.rejected}")


@app.command("export-parquet")
def export_parquet_cmd() -> None:
    """Write data/parquet/readings_{region}.parquet (training snapshot)."""
    from .ingest import export_parquet

    typer.echo(str(export_parquet(_ctx().store)))


@app.command("train")
def train_cmd(version: str = typer.Option(None), folds: int = typer.Option(None, help="0 = true LOSO"),
              verbose: bool = True) -> None:
    """Train the neighbor model on non-holdout seasons; writes data/models/{version}/metrics.json."""
    _setup(verbose)
    from .qc.model import train

    b = train(_ctx().store, version=version, loso_folds=folds)
    m = b.metrics
    typer.echo(json.dumps({"version": b.version, "fit": m["fit"], "loso_model": m["loso_model"],
                           "loso_idw_lapse": {"mae_f": m["loso_idw_lapse"]["mae_f"]}}, indent=2))


@app.command()
def detect(as_of: str = typer.Option(None), lookback_days: int = 7, json_out: bool = False,
           verbose: bool = False) -> None:
    """Run detection as of a time (default now) and print detections."""
    _setup(verbose)
    from .qc.detect import run_detection

    a = pd.Timestamp(as_of, tz="UTC") if as_of else pd.Timestamp.now(tz="UTC")
    dets = run_detection(a, lookback_days, ctx=_ctx())
    if json_out:
        typer.echo(json.dumps([d.to_dict() for d in dets], indent=2))
        return
    for d in dets:
        typer.echo(f"{d.station_id:>5} {d.type:<13} {d.severity:<8} {pd.Timestamp(d.start_ts)} -> "
                   f"{'ongoing' if d.end_ts is None else pd.Timestamp(d.end_ts)}  {d.evidence}")


@app.command()
def fill(start: str, end: str, verbose: bool = False) -> None:
    """Gap-fill [start, end] (writes used_temp_f, is_estimated, est_sd, est_method)."""
    _setup(verbose)
    from .repair.fill import fill_store

    gaps = fill_store(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), ctx=_ctx())
    typer.echo(f"{len(gaps)} gaps; {sum(g['estimated_hours'] for g in gaps)} hours estimated; "
               f"{sum(g['long_gap'] for g in gaps)} long gaps")


@app.command()
def daily(as_of: str = typer.Option(None), verbose: bool = False) -> None:
    """Daily job: detection + fill (run after `ingest --days 3`)."""
    _setup(verbose)
    from .pipeline import daily_job

    dets = daily_job(pd.Timestamp(as_of, tz="UTC") if as_of else None, ctx=_ctx())
    typer.echo(f"{len(dets)} detections")


@app.command("set-offset")
def set_offset(station: int, start: str, end: str) -> None:
    """Accept a station's offset from a window (grower confirmed the reading is expected)."""
    from .qc.model import set_offset_from_window

    r = set_offset_from_window(station, pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), ctx=_ctx())
    typer.echo(json.dumps(r, default=str, indent=2))


@app.command("learn-offsets")
def learn_offsets(as_of: str = typer.Option(None)) -> None:
    """Weekly job: per-station night/day median residual over the last 28 days of ok readings."""
    from .qc.model import update_learned_offsets

    a = pd.Timestamp(as_of, tz="UTC") if as_of else pd.Timestamp.now(tz="UTC").floor("h")
    typer.echo(f"{update_learned_offsets(a, ctx=_ctx())} station offsets updated")


@app.command()
def bench(faults: int = 100, seed: int = 42, gapfill_blocks: int = 200, max_days: int = typer.Option(None),
          verbose: bool = True) -> None:
    """Fault-injection benchmark on the held-out seasons -> bench/results/{timestamp}.json + .md."""
    _setup(verbose)
    from .bench.run import run_bench

    r = run_bench(faults, seed, ctx=_ctx(), gapfill_blocks=gapfill_blocks, max_days=max_days)
    typer.echo(Path(r["paths"][1]).read_text())
    typer.echo(f"written: {r['paths']}")


@app.command("blind-test")
def blind_test_cmd(station: int, season: str, retrain: bool = True, csv: Path = typer.Option(None)) -> None:
    """Hide a station entirely and predict it from neighbors (LOSO model)."""
    from .repair.virtual import blind_test

    r = blind_test(station, season, ctx=_ctx(), retrain=retrain)
    s = r.pop("series")
    if csv:
        s.to_csv(csv, index=False)
    typer.echo(json.dumps(r, indent=2, default=str))


@app.command("durham-rescue")
def durham(verbose: bool = True) -> None:
    """LOSO validation of Durham (12) over its pre-closure seasons."""
    _setup(verbose)
    from .repair.virtual import durham_rescue

    typer.echo(json.dumps(durham_rescue(_ctx()), indent=2, default=str))


@app.command()
def scenario(path: Path = typer.Option(Path("config/scenarios/demo_2025_26.yaml")),
             days: int = typer.Option(None, help="replay only the first N days"),
             out: Path = typer.Option(None, help="write detections JSON here"), verbose: bool = False) -> None:
    """Replay the demo scenario day by day and print each new detection."""
    _setup(verbose)
    from .demo.scenario import run_scenario

    res = run_scenario(path, ctx=_ctx(), days=days, echo=typer.echo)
    if out:
        out.write_text(json.dumps(res, indent=2, default=str))


@app.command("synthetic-demo")
def synthetic_demo(stations: int = 30, verbose: bool = False) -> None:
    """Offline dry run on SYNTHETIC data (no CIMIS key needed). Never present these numbers."""
    _setup(verbose)
    from .demo.scenario import synthetic_dry_run

    synthetic_dry_run(n_stations=stations, echo=typer.echo)


if __name__ == "__main__":
    app()



