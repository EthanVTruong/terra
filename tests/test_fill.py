import numpy as np
import pandas as pd

from chillwatch.context import Context
from chillwatch.repair.fill import fill, fill_store
from chillwatch.store import MemoryStore
from conftest import utc


def test_fill_pure():
    hours = pd.date_range(utc("2025-01-01 09:00"), periods=6, freq="h")
    r = pd.DataFrame({"station_id": 1, "ts": hours, "temp_f": [40, np.nan, 99, 41, 42, np.nan],
                      "qc_status": ["ok", "missing", "invalid", "ok", "suspect", "missing"]})
    e = pd.DataFrame({"station_id": 1, "ts": hours, "expected_f": [40.5, 41, 42, 43, 44, np.nan],
                      "est_sd": 1.0, "method": ["model"] * 5 + ["none"]})
    out, gaps = fill(r, e)
    assert out["used_temp_f"].tolist()[:5] == [40, 41, 42, 41, 42] and np.isnan(out["used_temp_f"].iloc[5])
    assert out["is_estimated"].tolist() == [False, True, True, False, False, False]
    assert out.loc[1, "est_method"] == "model"
    assert [g["hours"] for g in gaps] == [2, 1]


def test_long_gap_uses_widest_bin():
    hours = pd.date_range(utc("2025-01-01 09:00"), periods=24 * 15, freq="h")
    r = pd.DataFrame({"station_id": 1, "ts": hours, "temp_f": np.nan, "qc_status": "missing"})
    e = pd.DataFrame({"station_id": 1, "ts": hours, "expected_f": 45.0, "est_sd": 1.0, "method": "model"})
    out, gaps = fill(r, e, widest={"sd": 4.2})
    assert gaps[0]["long_gap"] and (out["est_sd"] == 4.2).all()


def test_fill_store(syn_stations, syn_readings, syn_model):
    r = syn_readings.copy()
    m = (r.station_id == 70) & (r.ts >= utc("2025-10-20 09:00")) & (r.ts < utc("2025-10-21 09:00"))
    truth = r.loc[m, "temp_f"].to_numpy()
    r.loc[m, ["temp_f"]] = np.nan
    r.loc[m, ["qc_base", "qc_status"]] = "missing"
    ctx = Context(MemoryStore(syn_stations, r), syn_model)
    fill_store(utc("2025-10-19 09:00"), utc("2025-10-22 08:00"), ctx=ctx)
    got = ctx.store.readings(utc("2025-10-20 09:00"), utc("2025-10-21 08:00"), [70])
    assert got["is_estimated"].all() and got["est_sd"].notna().all()
    assert np.abs(got["used_temp_f"].to_numpy() - truth).mean() < 3.0
