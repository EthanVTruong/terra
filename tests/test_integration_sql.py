"""End-to-end on PostgreSQL with a fake CIMIS API serving synthetic data (slow)."""

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from chillwatch.context import Context
from chillwatch.demo.synthetic import make_readings, make_stations
from chillwatch.ingest import backfill, export_parquet, sync_stations
from chillwatch.qc.detect import run_detection
from chillwatch.qc.model import predict_expected, train
from chillwatch.repair.fill import fill_store
from chillwatch.sources.cimis import BASE_URL, CimisSource
from chillwatch.store import SqlStore
from conftest import utc
from test_store_sql import URL


def fake_cimis(stations: pd.DataFrame, readings: pd.DataFrame):
    by = {sid: g.set_index("ts")["temp_f"] for sid, g in readings.groupby("station_id")}

    def station_list(request):
        out = []
        for r in stations.itertuples():
            out.append({"StationNbr": str(r.station_id), "Name": r.name, "County": "Test",
                        "ConnectDate": "1/1/1990", "DisconnectDate": "12/31/2050", "IsActive": "True",
                        "Elevation": str(int(r.elevation_ft)), "HmsLatitude": f"x / {r.lat}",
                        "HmsLongitude": f"x / {r.lon}"})
        return httpx.Response(200, json={"Stations": out})

    def data(request):
        q = request.url.params
        sid = int(q["targets"])
        s = by[sid]
        recs = []
        for d in pd.date_range(q["startDate"], q["endDate"], freq="D"):
            for h in range(1, 25):
                ts = (d + pd.Timedelta(hours=h + 8)).tz_localize("UTC")
                v = s.get(ts, np.nan)
                recs.append({"Date": f"{d:%Y-%m-%d}", "Hour": f"{h:02d}00", "Station": str(sid),
                             "HlyAirTmp": {"Value": None if pd.isna(v) else f"{v:.1f}", "Qc": "M" if pd.isna(v) else " ",
                                           "Unit": "(F)"}})
        return httpx.Response(200, json={"Data": {"Providers": [{"Name": "cimis", "Records": recs}]}})

    respx.get(f"{BASE_URL}/station").mock(side_effect=station_list)
    respx.get(f"{BASE_URL}/data").mock(side_effect=data)


@pytest.mark.slow
@respx.mock
def test_end_to_end_postgres(tmp_path):
    try:
        store = SqlStore(URL)
        with store.engine.begin() as c:
            c.exec_driver_sql("select 1")
    except Exception as e:
        pytest.skip(f"postgres unavailable: {e}")
    st = make_stations(12, seed=5)
    ids = st["station_id"].tolist()
    r = pd.concat([make_readings(st, "2022-10-01 09:00", "2022-12-01 08:00", seed=1),
                   make_readings(st, "2025-10-01 09:00", "2025-11-01 08:00", seed=2)])
    fake_cimis(st, r)
    with store.engine.begin() as c:
        c.exec_driver_sql("delete from readings where station_id = any(%(i)s)", {"i": ids})
        c.exec_driver_sql("delete from stations where station_id = any(%(i)s)", {"i": ids})
    try:
        src = CimisSource(app_key="k", cache_dir=tmp_path / "raw", min_interval_s=0, sleep=lambda s: None)
        region = sync_stations(src, store)
        assert {6, 70, 71, 39, 5, 12}.issubset(set(region["station_id"]))
        res = backfill(src, store, date(2022, 10, 1), date(2022, 11, 30), station_ids=ids)
        assert all(not isinstance(v, Exception) for v in res.values())
        res2 = backfill(src, store, date(2022, 10, 1), date(2022, 11, 30), station_ids=ids)  # resumable: cached
        assert res2 == res
        backfill(src, store, date(2025, 10, 1), date(2025, 10, 31), station_ids=ids)
        rp, sp = export_parquet(store, out_dir=tmp_path / "pq")
        assert len(pd.read_parquet(rp)) >= len(ids) * 61 * 24
        model = train(store, seasons=["2022-23"], version="it", loso_folds=2, out_dir=tmp_path / "models")
        assert model.metrics["loso_model"]["mae_f"] < 3
        ctx = Context(store, model)
        e = predict_expected(70, utc("2025-10-10 09:00"), utc("2025-10-11 08:00"), ctx=ctx)
        assert len(e) == 24 and (e["method"] == "model").all()
        # inject a stuck sensor directly in the DB and detect it
        stuck = store.readings(utc("2025-10-25 09:00"), utc("2025-10-26 08:00"), [71]).assign(temp_f=44.4)
        store.upsert_readings(stuck)
        dets = run_detection(utc("2025-10-27 08:00"), ctx=ctx)
        assert any(d.type == "stuck" and d.station_id == 71 for d in dets)
        fill_store(utc("2025-10-20 09:00"), utc("2025-10-27 08:00"), ctx=ctx)
        got = store.readings(utc("2025-10-25 09:00"), utc("2025-10-26 08:00"), [71])
        assert (got["qc_status"] == "invalid").all() and got["is_estimated"].all()
        reg = pd.read_sql("select version, is_active from model_registry where version = 'it'", store.engine)
        assert reg["is_active"].item()
    finally:
        with store.engine.begin() as c:
            c.exec_driver_sql("delete from readings where station_id = any(%(i)s)", {"i": ids})
            c.exec_driver_sql("delete from stations where station_id = any(%(i)s)", {"i": ids})
            c.exec_driver_sql("delete from model_registry where version = 'it'")
