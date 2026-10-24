import numpy as np
import pandas as pd

from chillwatch.context import Context
from chillwatch.qc.model import idw_only_bundle
from chillwatch.repair.grid import grid_cells, predict_grid, to_geojson, write_grid
from chillwatch.store import MemoryStore


def test_grid_cells_require_two_stations():
    st = pd.DataFrame({"lat": [37.0, 37.1, 39.0], "lon": [-120.0, -120.1, -122.0]})
    cells = grid_cells(st, km=10, max_km=80, min_stations=2)
    assert len(cells) > 0 and (cells["n_stations_within"] >= 2).all()
    # nothing near the lone northern station qualifies on its own
    assert not ((cells["lat"] > 38.5) & (cells["lon"] < -121.5) & (cells["n_stations_within"] < 2)).any()


def test_predict_grid_synthetic(tmp_path, syn_stations):
    from chillwatch.demo.synthetic import make_readings

    r = make_readings(syn_stations, "2025-10-01 09:00", "2025-12-15 08:00", seed=5)
    ctx = Context(MemoryStore(syn_stations, r), idw_only_bundle())

    def elev(lat, lon):
        return np.where(lon < -123.0, -200.0, 150.0)  # west of -123 is "ocean"

    df = predict_grid(ctx, "2025-26", km=25, elevation_fn=elev, n_boot=10)
    assert len(df) > 20 and df["valid"].any()
    v = df[df["valid"]]
    assert (v["chill_p10"] <= v["chill_hours"] + 1e-9).mean() > 0.8 and (v["chill_hours"] <= v["chill_p90"] + 1e-9).mean() > 0.8
    assert (df.loc[df["lon"] < -123.0, "reason"] == "water").all()
    assert v["chill_hours"].between(0, 24 * 45).all()  # Nov 1 - Dec 15
    gj = to_geojson(df)
    assert gj["features"][0]["geometry"]["type"] == "Polygon"
    paths = write_grid(df, syn_stations, tmp_path, label="synthetic")
    for p in paths.values():
        assert (tmp_path / p.split("/")[-1]).exists()
