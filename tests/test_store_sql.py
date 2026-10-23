import os

import numpy as np
import pandas as pd
import pytest

from chillwatch.store import SqlStore
from conftest import utc

URL = os.environ.get("CHILLWATCH_TEST_DB_URL", "postgresql+psycopg://chillwatch:chillwatch@localhost:5432/chillwatch")


@pytest.fixture()
def sql():
    try:
        s = SqlStore(URL)
        with s.engine.begin() as c:
            c.exec_driver_sql("delete from readings where station_id >= 900000")
            c.exec_driver_sql("delete from stations where station_id >= 900000")
    except Exception as e:  # no database available
        pytest.skip(f"postgres unavailable: {e}")
    yield s
    with s.engine.begin() as c:
        c.exec_driver_sql("delete from readings where station_id >= 900000")
        c.exec_driver_sql("delete from stations where station_id >= 900000")


def test_upsert_is_idempotent_and_keeps_latest(sql):
    sql.upsert_stations(pd.DataFrame([{"station_id": 900001, "source": "test", "source_station_id": "x",
                                       "name": "t", "county": "c", "lat": 38.0, "lon": -121.0,
                                       "elevation_ft": 10.0, "is_active": True, "connect_date": pd.NaT,
                                       "disconnect_date": pd.NaT, "in_region": True, "tz": None}]))
    hours = pd.date_range(utc("2025-01-01 09:00"), periods=3, freq="h")
    r = pd.DataFrame({"station_id": 900001, "ts": hours, "temp_f": [40.0, np.nan, 42.0],
                      "qc_base": ["ok", "missing", "ok"], "qc_status": ["ok", "missing", "ok"],
                      "ingested_at": utc("2025-01-02")})
    sql.upsert_readings(r)
    sql.upsert_readings(r)
    late = r.iloc[[1]].assign(temp_f=41.0, qc_base="ok", qc_status="ok", ingested_at=utc("2025-01-03"))
    sql.upsert_readings(late)
    got = sql.readings(hours[0], hours[-1], [900001])
    assert got["temp_f"].tolist() == [40.0, 41.0, 42.0]
    sql.update_readings(got.assign(qc_status="invalid"), ["qc_status"])
    assert (sql.readings(hours[0], hours[-1], [900001])["qc_status"] == "invalid").all()
    assert sql.last_observed(hours[-1])[900001] == hours[-1]
