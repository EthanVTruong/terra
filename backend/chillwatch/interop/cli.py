"""``chillwatch logger ...`` commands: capture, validate, chill summary, calibration, demo."""

from __future__ import annotations

import json
from pathlib import Path

import typer

logger_app = typer.Typer(no_args_is_help=True, help="Orchard logger: capture, validate, chill, report")


def _node(node_id: str, registry: Path | None):
    from .registry import get_node

    return get_node(node_id, registry)


def _write(packet: dict, scored, json_out: Path | None, html_out: Path | None) -> None:
    from .report import render

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(packet, indent=2, default=str))
        typer.echo(f"wrote {json_out}")
    if html_out:
        html_out.parent.mkdir(parents=True, exist_ok=True)
        html_out.write_text(render(packet, scored))
        typer.echo(f"wrote {html_out}")


def _headline(packet: dict) -> None:
    c = packet["chill"]
    h = c["hours"]
    typer.echo(f"chill portions {c['chill_portions']['with_estimates']:.1f} "
               f"(range {c['chill_portions']['p10']:.1f}-{c['chill_portions']['p90']:.1f}, measured only "
               f"{c['chill_portions']['observed_only']:.1f}); chill hours {c['chill_hours']['with_estimates']:.0f}")
    typer.echo(f"hours measured {h['observed_ok']}/{h['expected']} ({h['coverage_observed']:.1%}), "
               f"estimated {h['estimated']}, unfilled {h['unfilled']}; {len(c['gaps'])} gap(s)")


@logger_app.command("bridge")
def bridge(port: str = typer.Option(..., help="serial port, e.g. /dev/ttyACM0 or COM3"),
           out: Path = typer.Option(..., help="append JSONL here"), baud: int = 9600) -> None:
    """Stamp each line from the Uno with the host's UTC time and append it to a JSONL capture."""
    from .bridge import run_bridge

    run_bridge(port, out, baud)


@logger_app.command("validate")
def validate(capture: Path = typer.Argument(..., help=".jsonl capture or .csv/.json DIY log"),
             node: str = typer.Option(..., help="node_id in the registry"),
             registry: Path = typer.Option(None, help="default config/nodes.yaml"),
             start: str = typer.Option(None, help="UTC start (default: first sample)"),
             end: str = typer.Option(None, help="UTC end (default: last sample)"),
             no_reference: bool = typer.Option(False, help="skip the physics/ML cross-check"),
             target_portions: float = typer.Option(None, help="crop requirement in Chill Portions"),
             target_hours: float = typer.Option(None, help="crop requirement in chill hours"),
             json_out: Path = typer.Option(None), html_out: Path = typer.Option(None)) -> None:
    """Validate a capture, cross-check it against the reference stations and summarize chill."""
    from ..context import get_context
    from .pipeline import run
    from .readers import read_any

    n = _node(node, registry)
    samples, rejects = read_any(capture, n.node_id, n.tz)
    if samples.empty:
        raise typer.Exit(f"no samples for node {n.node_id} in {capture}")
    s = start or samples["received_at"].min()
    e = end or samples["received_at"].max()
    res = run(samples, rejects, n, None if no_reference else get_context(), s, e,
              target_portions=target_portions, target_hours=target_hours)
    packet = res.packet()
    _headline(packet)
    _write(packet, res.hourly, json_out, html_out)


@logger_app.command("chill")
def chill(capture: Path, node: str = typer.Option(...), season: str = typer.Option(..., help="e.g. 2026-27"),
          registry: Path = typer.Option(None), as_of: str = typer.Option(None, help="UTC; default now"),
          target_portions: float = typer.Option(None), target_hours: float = typer.Option(None),
          no_reference: bool = False, json_out: Path = typer.Option(None)) -> None:
    """Season chill totals (chill hours + Chill Portions) with coverage and gaps, up to now or --as-of."""
    import pandas as pd

    from ..chill import season_window
    from ..context import get_context
    from .pipeline import run
    from .readers import read_any

    n = _node(node, registry)
    samples, rejects = read_any(capture, n.node_id, n.tz)
    a, b = season_window(season)
    cut = pd.Timestamp(as_of, tz="UTC") if as_of else pd.Timestamp.now(tz="UTC")
    b = min(b, cut.floor("h"))       # a season in progress: future hours are not missing
    if b < a:
        raise typer.Exit(f"season {season} starts {a:%Y-%m-%d}; nothing to total yet")
    res = run(samples, rejects, n, None if no_reference else get_context(), a, b,
              target_portions=target_portions, target_hours=target_hours)
    out = res.chill
    typer.echo(json.dumps({k: out[k] for k in ("period", "hours", "complete", "chill_hours", "chill_portions")},
                          indent=2, default=str))
    if json_out:
        json_out.write_text(json.dumps(out, indent=2, default=str))


@logger_app.command("calibrate")
def calibrate(capture: Path, reference: Path = typer.Argument(..., help="reference thermometer CSV/JSON"),
              node: str = typer.Option(...), registry: Path = typer.Option(None),
              tolerance_c: float = 0.3, max_gap_s: float = 120.0) -> None:
    """The +-0.3 C calibration test: logger vs a co-located reference thermometer (0-10 C)."""
    from .calibration import compare
    from .readers import read_any, read_diy
    from .rules import validate_samples

    n = _node(node, registry)
    samples, _ = read_any(capture, n.node_id, n.tz)
    validated, _ = validate_samples(samples, n)
    ref, _ = read_diy(reference, "reference", n.tz)
    typer.echo(json.dumps(compare(validated, ref, tolerance_c, max_gap_s), indent=2))


@logger_app.command("demo")
def demo(out_dir: Path = typer.Option(Path("data/logger-demo")), with_model: bool = typer.Option(
        False, help="train a small SYNTHETIC ML model so the fusion uses both cores (slower)")) -> None:
    """End-to-end run on SYNTHETIC data with injected faults (no hardware or CIMIS key needed)."""
    from ..context import Context
    from ..demo.logger import make_logger_demo
    from ..qc.model import idw_only_bundle, train
    from ..store import MemoryStore
    from .bridge import append_jsonl
    from .pipeline import run
    from .readers import read_capture

    d = make_logger_demo()
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = out_dir / "orchard-1.jsonl"
    cap.unlink(missing_ok=True)
    append_jsonl(d.entries, cap)
    store = MemoryStore(d.ref_stations, d.ref_readings)
    model = idw_only_bundle()
    if with_model:
        # train on an earlier synthetic season of the reference stations (never the logger's window)
        from ..demo.synthetic import make_readings

        # a full winter, so the trees have seen frost nights (they cannot extrapolate past their labels)
        hist = make_readings(d.ref_stations, "2022-11-01 09:00", "2023-03-31 08:00", seed=3)
        model = train(MemoryStore(d.ref_stations, hist), seasons=["2022-23"], version="synthetic-demo",
                      loso_folds=2, out_dir=out_dir / "models", activate=False, evaluate_holdout=False)
    samples, rejects = read_capture(cap)
    res = run(samples, rejects, d.node, Context(store, model=model), samples["received_at"].min(),
              samples["received_at"].max(), target_portions=30.0)
    packet = res.packet()
    packet["synthetic"] = "SYNTHETIC DATA - for testing the pipeline only"
    _headline(packet)
    _write(packet, res.hourly, out_dir / "report.json", out_dir / "report.html")
