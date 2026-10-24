import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CHILLWATCH_CONFIG_DIR", str(ROOT / "config"))


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHILLWATCH_DATA_DIR", str(tmp_path / "data"))
    from chillwatch.context import set_context

    set_context(None)
    yield
    set_context(None)


@pytest.fixture(scope="session")
def syn_stations():
    from chillwatch.demo.synthetic import make_stations

    return make_stations(20, seed=0)


@pytest.fixture(scope="session")
def syn_model(tmp_path_factory, syn_stations):
    """Small synthetic model covering the configured season start through December."""
    from chillwatch import config
    from chillwatch.demo.synthetic import make_readings
    from chillwatch.qc.model import train
    from chillwatch.store import MemoryStore
    from chillwatch.timeutil import season_bounds

    out = tmp_path_factory.mktemp("models")
    seasons = config.region()["seasons"]
    start, _ = season_bounds("2022-23", seasons["start_mmdd"], seasons["end_mmdd"])
    r = make_readings(syn_stations, start.tz_localize(None), "2022-12-31 08:00", seed=3)
    return train(MemoryStore(syn_stations, r), seasons=["2022-23"], version="test", loso_folds=2, out_dir=out,
                 activate=False)


@pytest.fixture()
def syn_readings(syn_stations):
    from chillwatch.demo.synthetic import make_readings

    return make_readings(syn_stations, "2025-10-01 09:00", "2025-11-15 08:00", seed=11, missing_rate=0.0)


def utc(s):
    return pd.Timestamp(s, tz="UTC")
