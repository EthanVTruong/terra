import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from chillwatch.ingest import backfill, select_region, sync_stations
from chillwatch.sources.cimis import BASE_URL, CimisAuthError, CimisSource, parse_hms_coord
from chillwatch.store import MemoryStore
from conftest import utc

FIX = Path(__file__).parent / "fixtures"


def src(tmp_path, **kw):
    return CimisSource(app_key="k", cache_dir=tmp_path / "cache", min_interval_s=0, sleep=lambda s: None, **kw)


def test_parse_coord():
    assert parse_hms_coord("38º32'8N / 38.535694") == pytest.approx(38.535694)
    assert np.isnan(parse_hms_coord(None))


@respx.mock
def test_list_stations_and_region(tmp_path):
    respx.get(f"{BASE_URL}/station").mock(return_value=httpx.Response(200, json=json.loads((FIX / "cimis_stations.json").read_text())))
    s = src(tmp_path)
    st = s.list_stations()
    assert st.loc[st.source_station_id == "6", "lat"].item() == pytest.approx(38.535694)
    assert st.loc[st.source_station_id == "12", "is_active"].item() is False or not st.loc[st.source_station_id == "12", "is_active"].item()
    store = MemoryStore()
    region = sync_stations(s, store)
    ids = set(region["station_id"])
    assert {6, 12, 70}.issubset(ids)
    assert 147 not in ids  # San Diego is > 150 km from every seed
    # far-future disconnect date means still connected
    assert pd.isna(store.stations().set_index("station_id").at[6, "disconnect_date"])


@respx.mock
def test_fetch_hourly_parse_and_cache(tmp_path):
    payload = json.loads((FIX / "cimis_hourly_6.json").read_text())
    route = respx.get(f"{BASE_URL}/data").mock(return_value=httpx.Response(200, json=payload))
    s = src(tmp_path)
    df = s.fetch_hourly("6", date(2024, 1, 1), date(2024, 1, 2))
    assert len(df) == 48 and route.call_count == 1
    first = df.iloc[0]
    assert first["ts"] == utc("2024-01-01 09:00")
    assert np.isnan(df.loc[df["ts"] == utc("2024-01-01 13:00"), "temp_f"].item())  # hour 0500, QC M
    assert df["solar_wm2"].iloc[0] == pytest.approx(10 * 41840 / 86400)
    # cached: no second request
    s2 = src(tmp_path)
    df2 = s2.fetch_hourly("6", date(2024, 1, 1), date(2024, 1, 2))
    assert route.call_count == 1 and len(df2) == 48
    assert Path(df2["raw_ref"].iloc[0]).exists()


@respx.mock
def test_window_halving_on_refusal(tmp_path):
    payload = json.loads((FIX / "cimis_hourly_6.json").read_text())
    calls = []

    def handler(request):
        q = dict(request.url.params)
        calls.append((q["startDate"], q["endDate"]))
        if q["startDate"] != q["endDate"] and (date.fromisoformat(q["endDate"]) - date.fromisoformat(q["startDate"])).days > 1:
            return httpx.Response(400, json={"Message": "too many records"})
        return httpx.Response(200, json=payload)

    respx.get(f"{BASE_URL}/data").mock(side_effect=handler)
    s = src(tmp_path, window_days=4)
    s.fetch_hourly("6", date(2024, 1, 1), date(2024, 1, 4))
    assert calls[0] == ("2024-01-01", "2024-01-04")
    assert ("2024-01-01", "2024-01-02") in calls and ("2024-01-03", "2024-01-04") in calls


@respx.mock
def test_auth_error_not_halved(tmp_path):
    respx.get(f"{BASE_URL}/data").mock(return_value=httpx.Response(403))
    with pytest.raises(CimisAuthError):
        src(tmp_path).fetch_hourly("6", date(2024, 1, 1), date(2024, 1, 2))


@respx.mock
def test_one_failing_station_does_not_stop_others(tmp_path):
    payload = json.loads((FIX / "cimis_hourly_6.json").read_text())

    def handler(request):
        t = request.url.params["targets"]
        if t == "70":
            return httpx.Response(200, text="<html>not json</html>")
        p = json.loads(json.dumps(payload))
        for r in p["Data"]["Providers"][0]["Records"]:
            r["Station"] = t
        return httpx.Response(200, json=p)

    respx.get(f"{BASE_URL}/data").mock(side_effect=handler)
    st = pd.DataFrame({"station_id": [6, 70, 71], "source": "cimis", "source_station_id": ["6", "70", "71"],
                       "lat": 38.0, "lon": -121.0, "elevation_ft": 50.0, "is_active": True,
                       "connect_date": pd.Timestamp("1990-01-01"), "disconnect_date": pd.NaT, "in_region": True})
    store = MemoryStore(st)
    s = src(tmp_path)
    s._get.retry.stop = __import__("tenacity").stop_after_attempt(1)  # fail fast
    res = backfill(s, store, date(2024, 1, 1), date(2024, 1, 2))
    assert isinstance(res[70], Exception)
    assert res[6] == 48 and res[71] == 48
    r = store.readings()
    assert set(r["station_id"]) == {6, 71}
    assert (r.loc[r["temp_f"].isna(), "qc_status"] == "missing").all()


def test_select_region_includes_closed_durham():
    st = pd.DataFrame({"station_id": [6, 12, 999], "lat": [38.5, 39.6, 33.0], "lon": [-121.7, -121.8, -117.0],
                       "is_active": [True, False, True],
                       "disconnect_date": [pd.NaT, pd.Timestamp("2025-07-15"), pd.NaT]})
    region = {"seed_stations": [6], "radius_km": 150, "include_stations": [12], "recently_closed_since": "2005-10-01"}
    assert select_region(st, region).tolist() == [True, True, False]


@respx.mock
def test_firewall_rejection_is_auth_error(tmp_path):
    html = "<html><head><title>Request Rejected</title></head><body>The requested URL was rejected.</body></html>"
    route = respx.get(f"{BASE_URL}/data").mock(return_value=httpx.Response(200, text=html))
    with pytest.raises(CimisAuthError):
        src(tmp_path).fetch_hourly("6", date(2024, 1, 1), date(2024, 1, 2))
    assert route.call_count == 1  # not retried


def test_recorded_live_station_list():
    """Recorded from GET /api/station (2026-10-23)."""
    from chillwatch.sources.cimis import parse_stations

    st = parse_stations(json.loads((FIX / "cimis_stations_live.json").read_text()))
    assert len(st) == 276 and st["lat"].notna().all() and st["lon"].notna().all()
    assert st["lat"].between(32, 42).all() and st["lon"].between(-125, -114).all()
    d = st.set_index("source_station_id")
    assert d.at["6", "lat"] == pytest.approx(38.535694) and d.at["6", "elevation_ft"] == 60
    assert d.at["12", "disconnect_date"] == pd.Timestamp("2025-07-30") and not d.at["12", "is_active"]
