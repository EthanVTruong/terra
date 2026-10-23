import numpy as np
import pandas as pd
import pytest
from scipy.stats import theilslopes

from chillwatch.qc.neighbors import (
    adjusted_neighbor_temps,
    build_field,
    haversine_km,
    idw_baseline,
    nearest_neighbors,
    neighbor_schedule,
    theil_sen_lapse,
)
from conftest import utc


def test_haversine():
    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.195, abs=0.01)
    assert haversine_km(38.5, -121.7, 38.5, -121.7) == 0


def _stations():
    return pd.DataFrame({
        "station_id": [1, 2, 3, 4],
        "lat": [38.0, 38.1, 38.2, 39.5],
        "lon": [-121.0, -121.0, -121.0, -121.0],
        "elevation_ft": [100.0, 600.0, 1100.0, 50.0],
        "connect_date": [pd.Timestamp("2000-01-01"), pd.Timestamp("2000-01-01"), pd.Timestamp("2024-01-10"), pd.NaT],
        "disconnect_date": [pd.NaT, pd.Timestamp("2024-01-05"), pd.NaT, pd.NaT],
    })


def test_neighbors_respect_dates_and_radius():
    st = _stations()
    nb = nearest_neighbors(38.0, -121.0, 100.0, st, day="2024-01-01", exclude={1})
    assert nb["neighbor_id"].tolist() == [2]  # 3 not yet connected, 4 is ~167 km away
    assert nb["dz_ft"].iloc[0] == -500.0
    nb = nearest_neighbors(38.0, -121.0, 100.0, st, day="2024-01-20", exclude={1})
    assert nb["neighbor_id"].tolist() == [3]  # 2 disconnected after 2024-01-05


def test_schedule_switches_at_dates():
    st = _stations()
    hours = pd.date_range(utc("2024-01-03 09:00"), utc("2024-01-12 08:00"), freq="h")
    pos = {1: 0, 2: 1, 3: 2, 4: 3}
    idx, dist, dz = neighbor_schedule(38.0, -121.0, 100.0, st, hours, pos, exclude={1})
    assert idx[0, 0] == 1  # station 2 on Jan 3
    day = (hours.tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=9)).normalize()
    assert (idx[day == pd.Timestamp("2024-01-05"), 0] == 1).all()  # still connected on its disconnect date
    assert (idx[day == pd.Timestamp("2024-01-06"), 0] == -1).all()
    assert (idx[day == pd.Timestamp("2024-01-11"), 0] == 2).all()


@pytest.mark.parametrize("slope", [-3.5, 4.0])  # normal lapse and a valley inversion
def test_theil_sen_known_slope(slope):
    rng = np.random.default_rng(0)
    z = rng.uniform(0, 3000, 12)
    W = np.vstack([50 + slope * z / 1000 + rng.normal(0, 0.2, 12) for _ in range(5)])
    W[0, :8] = np.nan  # < 5 stations -> NaN
    g = theil_sen_lapse(W, z)
    assert np.isnan(g[0])
    for h in range(1, 5):
        ref = theilslopes(W[h], z)[0] * 1000
        assert g[h] == pytest.approx(ref, rel=1e-9)
        assert g[h] == pytest.approx(slope, abs=0.5)


def test_lapse_clip():
    z = np.array([0, 100, 200, 300, 400.0])
    W = np.array([[50, 52, 54, 56, 58.0]])  # 20 F / 1000 ft
    assert theil_sen_lapse(W, z)[0] == 10.0


def test_idw_math():
    adj = np.array([[50.0, 52.0, np.nan], [50.0, np.nan, np.nan]])
    dist = np.array([[10.0, 20.0, 5.0], [10.0, 20.0, 5.0]])
    tb, n = idw_baseline(adj, dist)
    w1, w2 = 1 / 100, 1 / 400
    assert tb[0] == pytest.approx((50 * w1 + 52 * w2) / (w1 + w2))
    assert np.isnan(tb[1]) and n.tolist() == [2, 1]


def test_lapse_adjustment_sign():
    W = np.array([[40.0, 45.0]])
    idx = np.array([[1]])
    dz = np.array([[1000.0]])  # target 1000 ft above the neighbor
    assert adjusted_neighbor_temps(W, idx, dz, np.array([3.0]))[0, 0] == 48.0  # inversion: warmer aloft
    assert adjusted_neighbor_temps(W, idx, dz, np.array([np.nan]))[0, 0] == 45.0  # missing gamma -> 0


def test_field_excludes_invalid_and_out_of_range(syn_stations):
    hours = pd.date_range(utc("2024-01-01 09:00"), periods=3, freq="h")
    r = pd.DataFrame({"station_id": [6, 6, 6], "ts": hours, "temp_f": [40.0, 200.0, 41.0],
                      "qc_base": ["ok", "ok", "invalid"]})
    f = build_field(r, syn_stations, hours[0], hours[-1], valid_col="qc_base")
    col = f.W[:, f.pos[6]]
    assert col[0] == 40 and np.isnan(col[1]) and np.isnan(col[2])
    assert f.raw[2, f.pos[6]] == 41
