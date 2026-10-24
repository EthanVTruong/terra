import numpy as np
import pandas as pd
import pytest

from chillwatch.estimate import ESTIMATE_COLUMNS, estimate_location, method_version

HOURS = pd.date_range("2026-01-11 09:00", periods=6, freq="h", tz="UTC")


def _stations(elev=(60.0, 82.0, 136.0, 7.0, 24.0)):
    # Terra's five stations (handoff geometry); ids as in CIMIS
    return pd.DataFrame({
        "station_id": [6, 226, 139, 212, 235],
        "lat": [38.535694, 38.672722, 38.501258, 38.278056, 38.797944],
        "lon": [-121.77636, -121.81172, -121.97853, -121.74111, -121.61136],
        "elevation_ft": list(elev),
    })


def _obs(temps=None, flags=None):
    temps = temps or {6: 37.0, 226: 36.9, 139: 38.5, 212: 34.0, 235: 36.1}
    rows = []
    for h in HOURS:
        for s, t in temps.items():
            rows.append({"station_id": s, "timestamp": h, "temperature_f": t, "quality": "usable",
                         "source_flag": (flags or {}).get(s, " ")})
    return pd.DataFrame(rows)


def _at_davis(**kw):
    args = dict(hours=HOURS, as_of=HOURS[-1], observations=_obs(), stations=_stations(),
                exclude_station_ids={6}, geometry_source="test")
    args.update(kw)
    return estimate_location(38.535694, -121.77636, 60.0, **args)


def _idw(lat, lon, z, st, temps, scale=600.0):
    from chillwatch.qc.neighbors import haversine_km

    d = haversine_km(lat, lon, st["lat"], st["lon"])
    w = 1 / d**2 * np.exp(-(((z - st["elevation_ft"].to_numpy()) / scale) ** 2))
    t = np.array([temps[s] for s in st["station_id"]])
    return float((w * t).sum() / w.sum())


def test_output_shape_and_value():
    out = _at_davis()
    assert list(out.columns) == ESTIMATE_COLUMNS
    assert (out["status"] == "ok").all() and (out["unit"] == "degF").all()
    st = _stations()
    peers = st[st["station_id"] != 6]
    expect = _idw(38.535694, -121.77636, 60.0, peers, {226: 36.9, 139: 38.5, 212: 34.0, 235: 36.1})
    assert out["temperature"].iloc[0] == pytest.approx(expect, abs=1e-9)
    assert sorted(out["used_station_ids"].iloc[0]) == [139, 212, 226, 235]
    assert out["method"].iloc[0] == "idw"  # Terra's 129 ft span is below the lapse gate
    assert out["method_version"].iloc[0] == method_version()
    assert {"station_id": 6, "reason": "held_out_target"} in out["excluded"].iloc[0]
    assert set(out["data_through_utc"].iloc[0]) == {139, 212, 226, 235}


def test_target_readings_never_used():
    obs = _obs()
    noisy = obs.copy()
    m = noisy["station_id"] == 6
    noisy.loc[m, "temperature_f"] = np.random.default_rng(0).uniform(-5, 110, m.sum())
    noisy.loc[m, "source_flag"] = "R"
    a, b = _at_davis(observations=obs), _at_davis(observations=noisy)
    pd.testing.assert_series_equal(a["temperature"], b["temperature"])


def test_terra_finding_excludes_neighbor_and_says_why():
    spike = _obs()
    spike.loc[(spike["station_id"] == 6) & (spike["timestamp"] == HOURS[0]), "temperature_f"] = 61.0
    findings = pd.DataFrame({"station_id": [6], "timestamp": [HOURS[0]], "reason": ["extreme_departure"]})
    st = _stations()
    woodland = dict(hours=HOURS, as_of=HOURS[-1], stations=st, exclude_station_ids={226})
    raw = estimate_location(38.672722, -121.81172, 82.0, observations=spike, **woodland)
    flagged = estimate_location(38.672722, -121.81172, 82.0, observations=spike, findings=findings, **woodland)
    dropped = estimate_location(38.672722, -121.81172, 82.0, observations=_obs().query("station_id != 6"),
                                **woodland)
    assert raw["temperature"].iloc[0] > 45  # the spike leaks in when nothing flags it
    assert dropped["temperature"].iloc[0] == pytest.approx(36.83, abs=0.01)  # matches the earlier replay check
    assert flagged["temperature"].iloc[0] == pytest.approx(dropped["temperature"].iloc[0], abs=1e-12)
    assert {"station_id": 6, "reason": "terra_finding:extreme_departure"} in flagged["excluded"].iloc[0]
    assert flagged["temperature"].iloc[1] == pytest.approx(raw["temperature"].iloc[1])  # other hours untouched


def test_findings_not_yet_visible_are_ignored():
    findings = pd.DataFrame({"station_id": [139], "timestamp": [HOURS[0]], "reason": ["x"],
                             "visible_at": [HOURS[-1] + pd.Timedelta(hours=5)]})
    out = _at_davis(findings=findings)
    assert 139 in out["used_station_ids"].iloc[0]


def test_source_flags_follow_terra_rule_by_default():
    out = _at_davis(observations=_obs(flags={139: "Y"}))
    assert {"station_id": 139, "reason": "source_flag:Y"} in out["excluded"].iloc[0]
    kept = _at_davis(observations=_obs(flags={139: "Y"}), usable_flags=("", "Y"))
    assert 139 in kept["used_station_ids"].iloc[0]


def test_as_of_is_causal_and_no_forecast():
    full = _obs()
    later = full.copy()
    later.loc[later["timestamp"] > HOURS[2], "temperature_f"] += 20  # data after as_of must not matter
    a = _at_davis(as_of=HOURS[2], observations=full)
    b = _at_davis(as_of=HOURS[2], observations=later)
    pd.testing.assert_series_equal(a["temperature"], b["temperature"])
    assert (a["status"].iloc[3:] == "insufficient_data").all()
    assert (a["status_reason"].iloc[3:] == "after_as_of").all()
    assert a["temperature"].iloc[3:].isna().all()


def test_too_few_peers():
    obs = _obs().query("station_id not in (212, 235)")
    out = _at_davis(observations=obs)
    assert (out["status"] == "insufficient_data").all()
    assert out["status_reason"].iloc[0] == "fewer_than_3_usable_peers"
    assert {"station_id": 212, "reason": "missing"} in out["excluded"].iloc[0]


def test_outside_validated_months(monkeypatch):
    from chillwatch import config

    hrs = pd.date_range("2026-07-01 09:00", periods=2, freq="h", tz="UTC")
    obs = _obs().assign(timestamp=lambda d: d["timestamp"] + (hrs[0] - HOURS[0]))
    assert (estimate_location(38.5, -121.8, 60.0, hrs, hrs[-1], obs, _stations())["status"] == "ok").all()
    monkeypatch.setitem(config.thresholds()["estimate"], "validated_months", [10, 11, 12, 1, 2, 3])
    out = estimate_location(38.5, -121.8, 60.0, hrs, hrs[-1], obs, _stations())
    assert (out["status_reason"] == "outside_validated_months").all()


def test_stale_when_older_than_max_age():
    out = _at_davis(max_age_hours=2)
    assert out["status"].tolist() == ["stale"] * 3 + ["ok"] * 3
    assert out["temperature"].notna().all()


def test_lapse_applies_only_with_elevation_spread():
    hilly = _stations(elev=(60.0, 900.0, 1500.0, 7.0, 2400.0))
    temps = {6: 40.0, 226: 37.0, 139: 34.0, 212: 41.0, 235: 31.0}
    out = estimate_location(38.6, -121.8, 300.0, HOURS, HOURS[-1], _obs(temps), hilly)
    assert (out["method"] == "idw_lapse").all()
    flat = estimate_location(38.6, -121.8, 300.0, HOURS, HOURS[-1], _obs(temps), _stations())
    assert (flat["method"] == "idw").all()


def test_hours_in_any_order_or_repeated():
    a = _at_davis()
    b = _at_davis(hours=list(HOURS[::-1]) + [HOURS[0]])
    pd.testing.assert_frame_equal(a, b)
    assert _at_davis(hours=[]).empty


def test_unknown_visibility():
    spike = _obs()
    spike.loc[(spike["station_id"] == 139) & (spike["timestamp"] == HOURS[0]), "temperature_f"] = 61.0
    findings = pd.DataFrame({"station_id": [139], "timestamp": [HOURS[0]], "reason": ["x"], "visible_at": [pd.NaT]})
    out = _at_davis(observations=spike, findings=findings)
    assert 139 not in out["used_station_ids"].iloc[0]  # a finding with unknown visibility still counts
    obs = _obs().assign(visible_at=lambda d: d["timestamp"])
    obs.loc[obs["station_id"] == 139, "visible_at"] = pd.NaT
    out = _at_davis(observations=obs)
    assert {"station_id": 139, "reason": "missing"} in out["excluded"].iloc[0]  # unknown -> not visible


def test_not_yet_visible_reading_looks_absent():
    obs = _obs().assign(visible_at=lambda d: d["timestamp"])
    late = obs["station_id"] == 139
    obs.loc[late, "visible_at"] = HOURS[-1] + pd.Timedelta(hours=3)
    a = _at_davis(observations=obs)
    b = _at_davis(observations=obs[~late])
    pd.testing.assert_frame_equal(a, b)


def test_revisions_use_latest_visible_and_duplicates_need_visible_at():
    obs = _obs().assign(visible_at=lambda d: d["timestamp"])
    rev = obs[(obs["station_id"] == 139) & (obs["timestamp"] == HOURS[0])].assign(
        quality="invalid", visible_at=HOURS[0] + pd.Timedelta(hours=1))
    out = _at_davis(observations=pd.concat([obs, rev]))
    assert {"station_id": 139, "reason": "quality:invalid"} in out["excluded"].iloc[0]
    with pytest.raises(ValueError):
        _at_davis(observations=pd.concat([_obs(), _obs().query("station_id == 139").head(1)]))


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        _at_davis(stations=pd.concat([_stations(), _stations().head(1)]))
    with pytest.raises(TypeError):
        _at_davis(exclude_station_ids="226")
    with pytest.raises(KeyError):
        _at_davis(observations=_obs().drop(columns=["quality"]))
    with pytest.raises(ValueError):
        _at_davis(as_of=None)


def test_version_tracks_flag_policy():
    a = _at_davis()["method_version"].iloc[0]
    b = _at_davis(usable_flags=("", "Y"))["method_version"].iloc[0]
    assert a != b


def test_non_numeric_temperature_is_missing():
    obs = _obs().astype({"temperature_f": object})
    obs.loc[(obs["station_id"] == 139) & (obs["timestamp"] == HOURS[0]), "temperature_f"] = "M"
    out = _at_davis(observations=obs)
    assert {"station_id": 139, "reason": "missing"} in out["excluded"].iloc[0]


def test_lapse_ignores_stations_beyond_max_km():
    far = pd.DataFrame({"station_id": [900], "lat": [40.5], "lon": [-121.8], "elevation_ft": [5000.0]})
    st = pd.concat([_stations(), far])
    obs = pd.concat([_obs(), _obs({900: 20.0}).query("station_id == 900")])
    out = _at_davis(stations=st, observations=obs)
    assert (out["method"] == "idw").all() and (out["lapse_station_ids"].map(len) == 0).all()


def test_intervals_only_when_enabled_and_calibrated(monkeypatch):
    import json

    from chillwatch import config

    assert _at_davis()[["p10", "p90"]].isna().all().all()  # off by default
    v_off = method_version()
    monkeypatch.setitem(config.thresholds()["estimate"], "intervals", True)
    assert _at_davis()[["p10", "p90"]].isna().all().all()  # on, but no calibrated bins yet
    st = {"sd": 1.0, "q10": -2.0, "q90": 1.5, "n": 1000, "bias": 0.0}
    d = config.data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    (d / "physics_fit.json").write_text(json.dumps({"uncertainty": {
        "_all": st, "_pooled_n": st, "_pooled_d": st, "_widest": st}}))
    out = _at_davis()
    assert np.allclose(out["p10"], out["temperature"] - 2.0) and np.allclose(out["p90"], out["temperature"] + 1.5)
    assert out["method_version"].iloc[0] != v_off  # the interval switch is part of the version
