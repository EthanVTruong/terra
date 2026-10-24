import json

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import GroupKFold

from chillwatch.context import Context
from chillwatch.qc.features import station_features
from chillwatch.qc.model import ModelBundle, idw_only_bundle, predict_expected, training_frame
from chillwatch.qc.neighbors import build_field
from chillwatch.store import MemoryStore


def test_features_never_use_target_value(syn_stations, syn_readings):
    r = syn_readings
    end = r.ts.min() + pd.Timedelta(days=5)
    X1, _ = station_features(build_field(r, syn_stations, r.ts.min(), end), 70)
    r2 = r.copy()
    rng = np.random.default_rng(1)
    m = r2.station_id == 70
    r2.loc[m, "temp_f"] = rng.uniform(-5, 110, m.sum())  # change only the target's own values
    X2, _ = station_features(build_field(r2, syn_stations, r.ts.min(), end), 70)
    pd.testing.assert_frame_equal(X1, X2)  # incl. gamma_h: the lapse fit excludes the target


def test_leak_assertion(syn_stations, syn_readings):
    from chillwatch.qc.features import location_features

    r = syn_readings
    f = build_field(r, syn_stations, r.ts.min(), r.ts.min() + pd.Timedelta(days=1))
    row = f.station_row(70)
    # without exclude the station is its own nearest neighbor; with exclude it never is
    X, _ = location_features(f, row.lat, row.lon, row.elevation_ft, exclude={70})
    assert np.isfinite(X["n1_dist"]).any() and (X["n1_dist"].dropna() > 0).all()


def test_loso_split_groups_by_station(syn_stations, syn_readings):
    r = syn_readings
    f = build_field(r, syn_stations, r.ts.min(), r.ts.min() + pd.Timedelta(days=3))
    tf = training_frame(f)
    for tr, va in GroupKFold(n_splits=4).split(tf, groups=tf["station_id"]):
        assert not set(tf.iloc[tr]["station_id"]) & set(tf.iloc[va]["station_id"])


def test_model_metrics_and_uncertainty(syn_model):
    m = syn_model.metrics
    assert m["loso_model"]["mae_f"] < 3.0
    assert set(m["loso_model"]["mae_by_distance"]) and m["validation"].startswith("GroupKFold")
    assert "_widest" in syn_model.unc_model


def test_predict_expected_columns_and_fallback(syn_stations, syn_readings, syn_model):
    ctx = Context(MemoryStore(syn_stations, syn_readings), syn_model)
    a = syn_readings.ts.min()
    df = predict_expected(70, a, a + pd.Timedelta(days=2), ctx=ctx)
    assert list(df.columns) == ["ts", "expected_f", "est_sd", "p10", "p90", "n_neighbors", "method"]
    assert (df["method"] == "model").all() and (df["p10"] <= df["expected_f"]).all()
    truth = syn_readings.set_index(["station_id", "ts"]).loc[70]["temp_f"].reindex(df["ts"]).to_numpy()
    assert np.nanmean(np.abs(truth - df["expected_f"])) < 3.0
    # no model -> physics baseline
    ctx2 = Context(MemoryStore(syn_stations, syn_readings), idw_only_bundle())
    assert (predict_expected(70, a, a + pd.Timedelta(days=1), ctx=ctx2)["method"] == "idw_lapse").all()
    # no neighbors at all -> none
    lonely = syn_readings[syn_readings.station_id == 70]
    ctx3 = Context(MemoryStore(syn_stations, lonely), syn_model)
    assert (predict_expected(70, a, a + pd.Timedelta(hours=5), ctx=ctx3)["method"] == "none").all()


def test_bundle_roundtrip(tmp_path, syn_model):
    syn_model.save(tmp_path / "m")
    b = ModelBundle.load(tmp_path / "m")
    X = pd.DataFrame(np.zeros((2, len(b.features))), columns=b.features)
    assert np.allclose(b.predict(X), syn_model.predict(X))
    assert b.oof_blocks().shape[1] == 24


def test_offsets_shift_expected(syn_stations, syn_readings, syn_model):
    from chillwatch.qc.model import compute_offset, set_offset_from_window

    a = syn_readings.ts.min()
    w = (a, a + pd.Timedelta(days=7))
    clean = Context(MemoryStore(syn_stations, syn_readings), syn_model)
    e = clean.expected(*w, [70], with_offsets=False)
    base_n, base_d, _ = compute_offset(syn_readings[syn_readings.station_id == 70], e)

    r = syn_readings.copy()
    r.loc[r.station_id == 70, "temp_f"] += 3.0  # station reads 3 F warm ("it's on a hill")
    ctx = Context(MemoryStore(syn_stations, r), syn_model)
    before = predict_expected(70, a + pd.Timedelta(days=10), a + pd.Timedelta(days=11), ctx=ctx)
    off = set_offset_from_window(70, *w, ctx=ctx)
    # the station's own values are not model inputs, so the learned offset moves by exactly 3 F
    assert off["night_f"] - base_n == pytest.approx(3.0, abs=1e-6)
    assert off["day_f"] - base_d == pytest.approx(3.0, abs=1e-6)
    after = predict_expected(70, a + pd.Timedelta(days=10), a + pd.Timedelta(days=11), ctx=ctx)
    # expected values move by the station's hour-of-day offsets (each also exactly +3 F vs clean)
    from chillwatch.qc.model import hourly_offsets

    hourly = json.loads(off["hourly_f"])
    base_h = hourly_offsets(syn_readings[syn_readings.station_id == 70], e, base_n, base_d)
    assert np.allclose(np.array(hourly) - np.array(base_h), 3.0, atol=1e-6)
    pst_hour = after["ts"].map(lambda t: (t.hour - 8) % 24)
    shift = after["expected_f"] - before["expected_f"]
    assert np.allclose(shift, pst_hour.map(lambda h: hourly[h]), atol=1e-6)
