import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import respx

from chillwatch.context import Context
from chillwatch.ingest import ingest_recent
from chillwatch.repair.fill import fill_store
from chillwatch.repair.uncertainty import bootstrap_traces
from chillwatch.repair.virtual import blind_test, virtual_station
from chillwatch.sources.cimis import BASE_URL, CimisSource
from chillwatch.store import MemoryStore
from conftest import utc

FIX = Path(__file__).parent / "fixtures"


def test_bootstrap_traces_only_perturb_estimated(syn_stations, syn_readings, syn_model):
    r = syn_readings.copy()
    m = (r.station_id == 70) & (r.ts >= utc("2025-10-20 09:00")) & (r.ts < utc("2025-10-22 09:00"))
    r.loc[m, "temp_f"] = np.nan
    r.loc[m, ["qc_base", "qc_status"]] = "missing"
    ctx = Context(MemoryStore(syn_stations, r), syn_model)
    fill_store(utc("2025-10-01 09:00"), utc("2025-11-15 08:00"), ctx=ctx, station_ids=[70])
    tr = bootstrap_traces(70, "2025-26", n=50, ctx=ctx)
    assert tr.shape[1] == 50
    est = tr.index.isin(r.loc[m, "ts"])
    assert (tr[~est].std(axis=1).fillna(0) < 1e-9).all()  # observed hours untouched
    assert (tr[est].std(axis=1) > 0).mean() > 0.9


def test_blind_test_and_virtual_station(syn_stations, syn_readings, syn_model):
    ctx = Context(MemoryStore(syn_stations, syn_readings), syn_model)
    res = blind_test(39, "2025-26", ctx=ctx, retrain=False)
    s = res["series"]
    assert list(s.columns[:5]) == ["ts", "actual", "predicted", "p10", "p90"]
    assert res["mae_f"] < 3.0 and res["n_hours"] > 24 * 30
    assert abs(res["chill_hours_predicted"] - res["chill_hours_actual"]) < 0.3 * max(res["chill_hours_actual"], 50)
    v = virtual_station(37.0, -120.2, 250.0, utc("2025-10-10 09:00"), utc("2025-10-11 08:00"), ctx=ctx)
    assert v["is_estimated"].all() and v["expected_f"].notna().all() and len(v) == 24


@respx.mock
def test_ingest_recent_upserts_late_data(tmp_path):
    payload = json.loads((FIX / "cimis_hourly_6.json").read_text())
    late = json.loads(json.dumps(payload))
    for rec in late["Data"]["Providers"][0]["Records"]:
        if rec["Date"] == "2024-01-01" and rec["Hour"] == "0500":
            rec["HlyAirTmp"] = {"Value": "38.5", "Qc": " ", "Unit": "(F)"}
    responses = iter([httpx.Response(200, json=payload), httpx.Response(200, json=late)])
    respx.get(f"{BASE_URL}/data").mock(side_effect=lambda req: next(responses))
    st = pd.DataFrame({"station_id": [6], "source": "cimis", "source_station_id": ["6"], "lat": 38.5,
                       "lon": -121.7, "elevation_ft": 60.0, "is_active": True,
                       "connect_date": pd.Timestamp("1982-07-17"), "disconnect_date": pd.NaT, "in_region": True})
    store = MemoryStore(st)
    src = CimisSource(app_key="k", cache_dir=tmp_path, min_interval_s=0, sleep=lambda s: None)
    ingest_recent(src, store, days=2, today=date(2024, 1, 3))
    h = utc("2024-01-01 13:00")
    assert store.readings(h, h)["qc_status"].item() == "missing"
    ingest_recent(src, store, days=2, today=date(2024, 1, 3))  # next day: CIMIS delivered the late hour
    r = store.readings(h, h)
    assert r["temp_f"].item() == 38.5 and r["qc_status"].item() == "ok"
    assert len(store.readings()) == 48  # no duplicates
