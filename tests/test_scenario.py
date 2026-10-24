"""Demo scenario on synthetic data: all four faults detected (slow: ~3 min)."""

from pathlib import Path

import pandas as pd
import pytest

from chillwatch import config
from chillwatch.context import Context
from chillwatch.demo.scenario import run_scenario
from chillwatch.demo.synthetic import make_readings
from chillwatch.store import MemoryStore
from chillwatch.timeutil import focus_bounds


@pytest.mark.slow
def test_demo_scenario_all_detected(syn_stations, syn_model):
    # synthetic readings over the window run_scenario replays: the Oct-Mar focus window, whose
    # first day is the scenario's day 0 (syn_model still trains from the configured season start)
    start, end = focus_bounds("2025-26")
    end = min(end, start + pd.Timedelta(days=75) - pd.Timedelta(hours=1))
    r = make_readings(syn_stations, start.tz_localize(None), end.tz_localize(None), seed=2025)
    ctx = Context(MemoryStore(syn_stations, r), syn_model)
    res = run_scenario(config.config_dir() / "scenarios" / "demo_2025_26.yaml", ctx, days=75, echo=lambda s: None)
    m = {x["type"]: x for x in res["matches"]}
    assert all(x["detected"] for x in res["matches"]), res["matches"]
    runs = {k: int(v["hours_to_detect"] // 24) + 1 for k, v in m.items()}
    # stuck: first daily run after onset
    assert runs["stuck"] == 1
    # outage: the 36 h offline rule cannot fire on run 1 for an outage that starts at 01:00 PST;
    # it fires on the first run with >= 36 h of silence (run 2)
    assert runs["outage"] <= 2
    assert runs["offset"] <= 3
    assert m["drift"]["detected_as"] in ("drift", "offset")
    # repaired series exists for the outage
    rep = res["repaired"]
    out = rep[(rep.station_id == 70) & rep.temp_f.isna()]
    assert out["is_estimated"].mean() > 0.9


def test_synthetic_cli_uses_configured_full_seasons_without_generating_or_training(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from typer.testing import CliRunner

    from chillwatch.cli import app
    from chillwatch.demo import scenario, synthetic
    from chillwatch.qc import model
    from chillwatch.timeutil import season_bounds

    monkeypatch.setenv("CHILLWATCH_DATA_DIR", str(tmp_path / "isolated-synthetic"))
    monkeypatch.setenv("CHILLWATCH_REGION", "region.yaml")
    monkeypatch.setenv("CHILLWATCH_CONFIG_DIR", str(Path(__file__).resolve().parents[1] / "config"))

    def one_reading(stations, start, end, *, seed):
        return pd.DataFrame({"station_id": [stations.iloc[0]["station_id"]],
                             "ts": [pd.Timestamp(start, tz="UTC")], "temp_f": [40.0]})

    make_readings_mock = Mock(side_effect=one_reading)
    bundle = SimpleNamespace(metrics={"loso_model": {"mae_f": 0.0}})
    train_mock = Mock(return_value=bundle)
    replay_mock = Mock(return_value={"synthetic_fixture": True})
    monkeypatch.setattr(synthetic, "make_readings", make_readings_mock)
    monkeypatch.setattr(model, "train", train_mock)
    monkeypatch.setattr(scenario, "run_scenario", replay_mock)

    result = CliRunner().invoke(app, ["synthetic-demo", "--stations", "6"])
    assert result.exit_code == 0, result.exception
    assert "SYNTHETIC DATA - pipeline dry run only" in result.output
    seasons = config.region()["seasons"]
    assert make_readings_mock.call_count == 3
    for call, year in zip(make_readings_mock.call_args_list, (2021, 2022, 2025), strict=True):
        expected = season_bounds(f"{year}-{str(year + 1)[2:]}", seasons["start_mmdd"], seasons["end_mmdd"])
        assert call.args[1:] == tuple(stamp.tz_localize(None) for stamp in expected)
        assert call.kwargs == {"seed": year}
        assert len(call.args[0]) == 6
    train_mock.assert_called_once()
    assert train_mock.call_args.kwargs["seasons"] == ["2021-22", "2022-23"]
    assert train_mock.call_args.kwargs["version"] == "synthetic"
    assert train_mock.call_args.kwargs["loso_folds"] == 3
    replay_mock.assert_called_once()
    assert replay_mock.call_args.args[0].name == "demo_2025_26.yaml"
    assert replay_mock.call_args.args[1].model is bundle
    assert replay_mock.call_args.kwargs["days"] == 80
