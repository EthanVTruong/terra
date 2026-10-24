"""Independent raw-provider boundary checks; no live calls or natural fault labels.

The source double saves CIMIS-shaped raw responses just as the native adapter
does. Its parsed numerical column is deliberately wrong, so these checks also
prove that live decisions use verified raw unit/QC evidence.
"""
from __future__ import annotations

import copy
from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chillwatch.sources.base import HOURLY_COLUMNS, STATION_COLUMNS
from chillwatch.sources.cimis import CimisAuthError
from terra_monitoring.live import LiveQueryError, LiveQueryService, normalize_cimis_payload


HOURS = pd.date_range("2026-01-11T09:00:00Z", periods=8, freq="h")
SITE = {"lat": 38.6, "lon": -121.84, "elevation_ft": 70.0,
        "location_id": "controlled-orchard"}
GEOMETRY = [
    (6, "Davis", 38.535694, -121.77636, 60.0),
    (226, "Woodland", 38.672722, -121.81172, 82.0),
    (139, "Winters", 38.501258, -121.97853, 136.0),
    (212, "Hastings Tract East", 38.278056, -121.74111, 7.0),
    (235, "Verona", 38.797944, -121.61136, 24.0),
]
IDS = [row[0] for row in GEOMETRY]
MISSING = object()


def raw_record(station=6, stamp=HOURS[-1], value="40.0", unit="F", qc=" ", **changes):
    """Construct hour-ending fixed-PST records, including CIMIS's 2400 spelling."""
    local = pd.Timestamp(stamp).tz_convert("Etc/GMT+8")
    if local.hour == 0:
        local_date = (local - pd.Timedelta(days=1)).date()
        hour = "2400"
    else:
        local_date = local.date()
        hour = f"{local.hour:02d}00"
    item = {"Value": value}
    if unit is not MISSING:
        item["Unit"] = unit
    if qc is not MISSING:
        item["Qc"] = qc
    return {"Station": str(station), "Date": local_date.isoformat(), "Hour": hour,
            "Scope": "hourly", "HlyAirTmp": item, **changes}


def envelope(records):
    return {"Data": {"Providers": [{"Name": "CIMIS", "Records": records}]}}


def baseline_records(hours=HOURS):
    return [raw_record(sid, stamp) for stamp in hours for sid in IDS]


def catalog():
    return pd.DataFrame([
        {"source": "cimis", "source_station_id": str(sid), "name": name,
         "county": "controlled", "lat": lat, "lon": lon, "elevation_ft": elevation,
         "is_active": True, "connect_date": "1980-01-01", "disconnect_date": None}
        for sid, name, lat, lon, elevation in GEOMETRY
    ], columns=STATION_COLUMNS)


class Clock:
    def __init__(self, value="2026-01-11T16:10:00Z"):
        self.value = pd.Timestamp(value)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += pd.Timedelta(**kwargs)


class SavedRawSource:
    """No network, secrets, ingestion, store, fitting or altered shared sources."""
    source = "cimis"
    data_mode = "synthetic"

    def __init__(self, tmp_path, records=None):
        self.root = Path(tmp_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.root
        self.records = baseline_records() if records is None else records
        self.stations = catalog()
        self.calls = []
        self.error = None
        self.catalog_error = None
        self.hourly_error = None
        self.payload_override = MISSING
        self.raw_body = None
        self.serial = 0

    def list_stations(self, refresh=False):
        self.calls.append(("stations", refresh))
        if self.catalog_error:
            raise self.catalog_error
        if self.error:
            raise self.error
        return self.stations.copy(deep=True)

    def fetch_hourly(self, station_id: str, start: date, end: date, refresh=False):
        self.calls.append(("hourly", station_id, start, end, refresh))
        if self.hourly_error:
            raise self.hourly_error
        if self.error:
            raise self.error
        requested = {int(value) for value in str(station_id).split(",")}
        records = [copy.deepcopy(row) for row in self.records if int(row["Station"]) in requested]
        self.serial += 1
        path = self.root / f"response-{self.serial}.json"
        payload = envelope(records) if self.payload_override is MISSING else self.payload_override
        body = json.dumps(payload, allow_nan=False) if self.raw_body is None else self.raw_body
        path.write_text(body, encoding="utf-8")
        # The real native parser discards unit metadata. A live consumer must
        # re-read raw_ref instead of treating this parsed number as verified.
        return pd.DataFrame([
            {"source": "cimis", "source_station_id": str(sid), "ts": HOURS[-1],
             "temp_f": 777.0, "qc_source": "", "raw_ref": str(path)}
            for sid in requested
        ], columns=HOURLY_COLUMNS)


def service(tmp_path, *, records=None, clock=None):
    source = SavedRawSource(tmp_path / "raw", records)
    clock = clock or Clock()
    live = LiveQueryService(app_key="", source=source, data_dir=tmp_path / "live", clock=clock,
                            ttl_seconds=120)
    return live, source, clock


def at_davis():
    return {"lat": 38.535694, "lon": -121.77636, "elevation_ft": 60.0,
            "target_station_id": 6, "location_id": "controlled-Davis"}


def finding(packet, sid=6):
    return [row for row in packet["station_findings"] if row["station_id"] == sid]


def moderate_records():
    """Values inside the frozen sustained band, below its extreme threshold."""
    from terra_qc import qc
    from terra_qc.reference import load_reference

    obs = pd.DataFrame([{
        "station_id": sid, "timestamp": stamp, "temperature_f": 40.0,
        "quality": "usable", "source_flag": "", "source": "cimis",
        "temperature_unit": "F", "timestamp_verified": True,
    } for stamp in HOURS for sid in IDS])
    target = qc.score(obs, load_reference()).query("station_id == 6").set_index("timestamp")
    records = baseline_records()
    for row in records:
        if row["Station"] == "6":
            # Map the independently generated record back to its known source
            # hour without calling the new provider normalizer under test.
            stamp = pd.Timestamp(row["Date"], tz="Etc/GMT+8") + pd.Timedelta(hours=int(row["Hour"]) // 100)
            expected = target.loc[stamp.tz_convert("UTC")]
            residual = (expected.sustained_high_residual_f + expected.extreme_abs_residual_f) / 2
            row["HlyAirTmp"]["Value"] = str(expected.expected_f + residual)
    return records


def test_context_is_network_free_and_reports_fixed_station_presets(tmp_path):
    live, source, _ = service(tmp_path)
    context = live.context()
    assert source.calls == []
    assert context["schema_version"] == "terra-live-context-v1"
    assert context["configured"] is True
    assert {row["station_id"] for row in context["station_presets"]} == set(IDS)
    json.dumps(context, allow_nan=False)


def test_live_arbitrary_location_uses_raw_values_and_has_no_invented_local_reading(tmp_path):
    live, source, clock = service(tmp_path)
    result = live.assess(dict(SITE))
    assert result["schema_version"] == "terra-location-assessment-v1"
    assert result["data_mode"] == "synthetic"
    assert result["location_estimate"]["status"] == "ok"
    assert result["location_estimate"]["temperature"] == pytest.approx(40.0)
    assert result["target_comparison"]["reason"] == "no_independent_target_observation"
    assert result["target_comparison"]["observed_f"] is None
    assert result["target_comparison"]["departure_f"] is None
    assert pd.Timestamp(result["query"]["as_of_utc"]) == clock.value
    assert pd.Timestamp(result["query"]["valid_utc"]) == HOURS[-1]
    assert any(call[0] == "hourly" for call in source.calls)
    json.dumps(result, allow_nan=False)


def test_controlled_extreme_is_flagged_and_excluded_for_location(tmp_path):
    records = baseline_records()
    records[-5]["HlyAirTmp"]["Value"] = "120.0"  # Davis at final hour.
    live, _, _ = service(tmp_path, records=records)
    result = live.assess(dict(SITE))
    assert len(finding(result)) == 1
    assert finding(result)[0]["reason"] == "extreme_departure"
    assert finding(result)[0]["severity"] == "strong"
    assert 6 not in result["location_estimate"]["used_station_ids"]
    assert any(row["station_id"] == 6 and row["reason"].startswith("terra_finding:")
               for row in result["location_estimate"]["excluded"])
    assert result["target_comparison"]["observed_f"] is None


def test_live_measured_location_compares_actual_reading_and_holds_target_out(tmp_path):
    records = baseline_records()
    records[-5]["HlyAirTmp"]["Value"] = "120.0"
    live, _, _ = service(tmp_path, records=records)
    result = live.assess(at_davis())
    target = result["target_comparison"]
    assert target["status"] == "assessable"
    assert target["classification"] == "suspected_anomaly"
    assert target["observed_f"] == 120.0
    assert target["departure_f"] == pytest.approx(120.0 - target["expected_f"])
    assert 6 not in result["location_estimate"]["used_station_ids"]
    assert result["provenance"]["peer_exclusion_basis"] == "target_held_out"
    assert len(finding(result)) == 1


@pytest.mark.parametrize("count,flagged", [(4, False), (5, True)])
def test_sustained_threshold_requires_five_eligible_readings(tmp_path, count, flagged):
    rows = moderate_records()[:count * len(IDS)]
    live, _, _ = service(tmp_path, records=rows)
    result = live.assess(dict(SITE))
    assert bool(finding(result)) is flagged
    if flagged:
        event = finding(result)[0]
        assert pd.Timestamp(event["start_utc"]) == HOURS[0]
        assert pd.Timestamp(event["detected_at_utc"]) == HOURS[4]


@pytest.mark.parametrize("break_kind", ["gap", "warning", "peer_loss"])
def test_gaps_warnings_and_peer_loss_break_sustained_sequence(tmp_path, break_kind):
    rows = moderate_records()[:6 * len(IDS)]
    if break_kind == "gap":
        del rows[3 * len(IDS):4 * len(IDS)]
    elif break_kind == "warning":
        rows[3 * len(IDS)]["HlyAirTmp"]["Qc"] = "Y"
    else:
        rows = [row for row in rows if not (row["Hour"] == "0400" and row["Station"] in {"139", "212", "235"})]
    live, _, _ = service(tmp_path, records=rows)
    result = live.assess(dict(SITE))
    assert not finding(result)


def test_current_peer_loss_does_not_fall_back_to_older_good_hour(tmp_path):
    rows = baseline_records()
    rows = [row for row in rows if not (row["Hour"] == "0800" and row["Station"] in {"139", "212", "235"})]
    live, _, _ = service(tmp_path, records=rows)
    result = live.assess(at_davis())
    assert pd.Timestamp(result["query"]["valid_utc"]) == HOURS[-1]
    assert result["location_estimate"]["status"] == "insufficient_data"
    assert result["location_estimate"]["temperature"] is None
    assert result["target_comparison"]["classification"] is None
    assert not result["station_findings"]


def test_far_location_does_not_invent_station_coverage(tmp_path):
    live, _, _ = service(tmp_path)
    result = live.assess({"lat": 36.0, "lon": -120.0, "elevation_ft": 100.0})
    assert result["location_estimate"]["status"] == "insufficient_data"
    assert result["location_estimate"]["temperature"] is None
    assert result["target_comparison"]["observed_f"] is None
    json.dumps(result, allow_nan=False)


def test_cached_query_uses_current_clock_without_refetching(tmp_path):
    live, source, clock = service(tmp_path)
    first = live.assess(dict(SITE))
    calls = list(source.calls)
    clock.advance(seconds=30)
    second = live.assess(dict(SITE))
    assert source.calls == calls
    meta = second["provenance"]["live_source"]
    assert meta["status"] == "cached"
    assert pd.Timestamp(meta["checked_at_utc"]) == clock.value
    assert pd.Timestamp(meta["retrieved_at_utc"]) == pd.Timestamp(first["provenance"]["live_source"]["retrieved_at_utc"])
    assert meta["cache_age_seconds"] == pytest.approx(30)
    assert pd.Timestamp(second["query"]["as_of_utc"]) == clock.value


def test_forced_refresh_acquires_new_snapshot_and_exposes_new_flag(tmp_path):
    live, source, clock = service(tmp_path)
    before = live.assess(dict(SITE))
    old_calls = len(source.calls)
    source.records[-5]["HlyAirTmp"]["Value"] = "120.0"
    clock.advance(seconds=30)
    after = live.assess({**SITE, "refresh": True})
    assert len(source.calls) > old_calls
    assert after["provenance"]["live_source"]["status"] == "refreshed"
    assert before["provenance"]["data_snapshot_id"] != after["provenance"]["data_snapshot_id"]
    assert not finding(before) and finding(after)
    assert pd.Timestamp(finding(after)[0]["visible_at_utc"]) >= clock.value


def test_failed_refresh_keeps_honest_old_age_and_sanitizes_provider_error(tmp_path):
    live, source, clock = service(tmp_path)
    first = live.assess(at_davis())
    secret_marker = "PRIVATE-KEY-DO-NOT-ECHO"
    source.hourly_error = RuntimeError(f"https://provider.invalid?appKey={secret_marker}")
    clock.advance(hours=7)
    result = live.assess({**at_davis(), "refresh": True})
    meta = result["provenance"]["live_source"]
    assert meta["status"] == "cached_after_error"
    assert meta["cache_age_seconds"] >= 7 * 3600
    assert pd.Timestamp(meta["retrieved_at_utc"]) == pd.Timestamp(first["provenance"]["live_source"]["retrieved_at_utc"])
    assert pd.Timestamp(meta["last_attempt_at_utc"]) == clock.value
    assert pd.Timestamp(result["query"]["as_of_utc"]) == clock.value
    assert result["location_estimate"]["status"] == "stale"
    assert result["target_comparison"]["status"] == "stale"
    assert result["target_comparison"]["classification"] is None
    assert secret_marker not in json.dumps(result, allow_nan=False)


def test_initial_provider_failure_is_structured_without_exception_leakage(tmp_path):
    live, source, _ = service(tmp_path)
    source.error = RuntimeError("provider failure; appKey=PRIVATE-SECRET-EXAMPLE")
    with pytest.raises(LiveQueryError) as raised:
        live.assess(dict(SITE))
    error = raised.value
    assert error.status_code == 503
    assert error.retryable is True
    assert error.code == "provider_unavailable"
    assert "PRIVATE-SECRET" not in str(error)


@pytest.mark.parametrize("payload", [{"Data": None}, {"Data": []}, {"Data": {"Providers": [{"Records": "broken"}]}}])
def test_malformed_provider_refresh_fails_closed_even_when_a_cached_packet_exists(tmp_path, payload):
    live, source, clock = service(tmp_path)
    live.assess(dict(SITE))
    source.payload_override = payload
    clock.advance(seconds=30)
    with pytest.raises(LiveQueryError) as raised:
        live.assess({**SITE, "refresh": True})
    assert raised.value.code == "provider_invalid_response"
    assert raised.value.status_code == 503
    assert "broken" not in str(raised.value)


def test_catalog_auth_failure_is_not_hidden_by_a_prior_catalog_or_cached_packet(tmp_path):
    live, source, clock = service(tmp_path)
    live.assess(dict(SITE))
    hourly_before = sum(call[0] == "hourly" for call in source.calls)
    source.catalog_error = CimisAuthError("rejected appKey=PRIVATE-CATALOG-KEY")
    clock.advance(seconds=30)
    with pytest.raises(LiveQueryError) as raised:
        live.assess({**SITE, "refresh": True})
    assert raised.value.code == "provider_auth"
    assert raised.value.status_code == 503
    assert raised.value.retryable is False
    assert sum(call[0] == "hourly" for call in source.calls) == hourly_before
    assert "PRIVATE-CATALOG-KEY" not in str(raised.value)


@pytest.mark.parametrize("raw_body", ["<html>PRIVATE-RESPONSE-ERROR</html>", '{"Data": {"Providers": [NaN]}}'])
def test_invalid_json_refresh_is_not_treated_as_a_transient_network_failure(tmp_path, raw_body):
    live, source, clock = service(tmp_path)
    live.assess(dict(SITE))
    source.raw_body = raw_body
    clock.advance(seconds=30)
    with pytest.raises(LiveQueryError) as raised:
        live.assess({**SITE, "refresh": True})
    assert raised.value.code == "provider_invalid_response"
    assert raised.value.status_code == 503
    assert "PRIVATE-RESPONSE-ERROR" not in str(raised.value)


def normalized(records, *, observed_at="2026-01-11T16:10:00Z", requested=IDS):
    return normalize_cimis_payload(envelope(records), requested_station_ids=requested,
                                   observed_at=pd.Timestamp(observed_at))


def test_explicit_fahrenheit_and_celsius_are_converted_with_verified_provenance():
    frame, audit = normalized([
        raw_record(6, value="40", unit="F"), raw_record(226, value="0", unit="C"),
    ])
    assert audit["received_records"] == 2
    assert audit["parsed_rows"] == 2
    assert audit["quarantined"] == []
    assert audit["source_unavailable_rows"] == 0
    by_station = frame.set_index("station_id")
    assert by_station.loc[6, "temperature_f"] == pytest.approx(40)
    assert by_station.loc[226, "temperature_f"] == pytest.approx(32)
    assert set(frame["temperature_unit"]) == {"F"}
    assert frame["timestamp_verified"].all()
    assert set(frame["quality"]) == {"usable"}
    assert set(frame["source"]) == {"cimis"}


def test_all_fixed_pst_hour_endings_and_2400_work_without_summer_dst_shift():
    rows = [raw_record(Hour=f"{hour:02d}00", Date="2026-07-01") for hour in range(1, 25)]
    frame, audit = normalized(rows, observed_at="2026-07-02T08:01:00Z")
    assert audit["parsed_rows"] == 24
    actual = pd.DatetimeIndex(frame.sort_values("timestamp")["timestamp"])
    expected = pd.date_range("2026-07-01T09:00:00Z", periods=24, freq="h")
    pd.testing.assert_index_equal(actual, expected, check_names=False, check_freq=False)
    assert actual[-1] == pd.Timestamp("2026-07-02T08:00:00Z")


@pytest.mark.parametrize("changes", [
    {"Hour": "0000"}, {"Hour": "100"}, {"Hour": "0101"}, {"Hour": "2500"},
    {"Hour": 100}, {"Hour": True}, {"Date": "2026-02-30"},
    {"Date": "2026-1-11"}, {"Date": "2026-01-11T00:00:00"},
    {"Scope": "daily"}, {"Scope": None}, {"Station": "999"}, {"Station": True},
])
def test_wrong_scope_station_date_or_noncanonical_hour_is_quarantined(changes):
    frame, audit = normalized([raw_record(**changes)])
    assert frame.empty
    assert audit["received_records"] == 1
    assert audit["parsed_rows"] == 0
    assert len(audit["quarantined"]) == 1
    assert audit["quarantined"][0]["reason"]


def test_future_source_rows_are_not_visible_in_current_snapshot():
    frame, audit = normalized([
        raw_record(stamp=HOURS[-1]),
        raw_record(stamp=HOURS[-1] + pd.Timedelta(hours=1), value="120"),
    ])
    assert len(frame) == 1
    assert pd.Timestamp(frame.iloc[0]["timestamp"]) == HOURS[-1]
    assert frame.iloc[0]["temperature_f"] == 40.0
    assert audit["received_records"] == 2


@pytest.mark.parametrize("unit", [MISSING, None, "", "Kelvin", "unknown", "C / F"])
def test_unknown_or_missing_temperature_units_never_become_fahrenheit(unit):
    frame, audit = normalized([raw_record(value="40", unit=unit)])
    assert len(frame) == 1
    assert pd.isna(frame.iloc[0]["temperature_f"])
    assert frame.iloc[0]["quality"] != "usable"
    assert audit["source_unavailable_rows"] == 1


@pytest.mark.parametrize("flag", ["Y", "R", "M", "Q", "H", "D", "new-warning", " Y "])
def test_every_nonblank_provider_warning_is_withheld(flag):
    frame, audit = normalized([raw_record(qc=flag)])
    assert len(frame) == 1
    assert frame.iloc[0]["quality"] != "usable"
    assert str(frame.iloc[0]["source_flag"]).strip() == flag.strip()
    assert audit["source_unavailable_rows"] == 1


@pytest.mark.parametrize("qc", [MISSING, None])
def test_absent_quality_evidence_is_not_an_explicit_blank_flag(qc):
    frame, audit = normalized([raw_record(qc=qc)])
    assert len(frame) == 1
    assert frame.iloc[0]["quality"] != "usable"
    assert audit["source_unavailable_rows"] == 1


@pytest.mark.parametrize("value", [True, False, None, "", "NaN", "Infinity", float("nan"), float("inf"), -float("inf")])
def test_bool_missing_or_nonfinite_temperatures_are_not_observations(value):
    frame, audit = normalized([raw_record(value=value)])
    assert len(frame) == 1
    assert pd.isna(frame.iloc[0]["temperature_f"])
    assert frame.iloc[0]["quality"] != "usable"
    assert audit["source_unavailable_rows"] == 1


def test_missing_temperature_item_retains_hour_as_unavailable_evidence():
    row = raw_record()
    del row["HlyAirTmp"]
    frame, audit = normalized([row])
    assert len(frame) == 1
    assert pd.Timestamp(frame.iloc[0]["timestamp"]) == HOURS[-1]
    assert pd.isna(frame.iloc[0]["temperature_f"])
    assert frame.iloc[0]["quality"] != "usable"
    assert audit["source_unavailable_rows"] == 1


def test_equivalent_duplicates_collapse_but_conflicts_have_no_invented_revision_order():
    row = raw_record(value="32", unit="F")
    equivalent = raw_record(value="0", unit="C")
    frame, audit = normalized([row, equivalent])
    assert len(frame) == 1 and frame.iloc[0]["temperature_f"] == 32.0
    assert audit["duplicate_groups"] == 1
    assert audit["conflicting_groups"] == 0
    conflicting = raw_record(value="120", unit="F")
    for rows in ([row, conflicting], [conflicting, row]):
        frame, audit = normalized(rows)
        assert len(frame) == 1
        assert pd.isna(frame.iloc[0]["temperature_f"])
        assert frame.iloc[0]["quality"] != "usable"
        assert audit["duplicate_groups"] == 1
        assert audit["conflicting_groups"] == 1
        assert audit["source_unavailable_rows"] == 1


@pytest.mark.parametrize("payload", [
    None, [], "provider html error", {}, {"Data": None}, {"Data": []},
    {"Data": {"Providers": "bad"}}, {"Data": {"Providers": [{}]}},
    {"Data": {"Providers": [{"Records": "bad"}]}},
])
def test_malformed_provider_envelopes_fail_closed_with_sanitized_error(payload):
    with pytest.raises(LiveQueryError) as raised:
        normalize_cimis_payload(payload, requested_station_ids=IDS,
                                observed_at=pd.Timestamp("2026-01-11T16:10:00Z"))
    assert raised.value.code == "provider_invalid_response"
    assert raised.value.status_code == 503
    assert "provider html error" not in str(raised.value)


@pytest.mark.parametrize("field,value", [("connect_date", "2026-01-12"), ("disconnect_date", "2026-01-10")])
def test_operating_dates_prevent_using_inactive_sources(tmp_path, field, value):
    live, source, _ = service(tmp_path)
    source.stations.loc[source.stations.source_station_id.eq("6"), field] = value
    result = live.assess(dict(SITE))
    assert 6 not in result["location_estimate"]["used_station_ids"]
    assert not finding(result)
    assert {"station_id": 6, "reason": "station_not_operating"} in result["location_estimate"]["excluded"]


def test_changed_calibrated_geometry_fails_closed_instead_of_reusing_old_thresholds(tmp_path):
    live, source, _ = service(tmp_path)
    source.stations.loc[source.stations.source_station_id.eq("6"), "lat"] = 38.7
    with pytest.raises(LiveQueryError) as raised:
        live.assess(dict(SITE))
    assert raised.value.code == "source_geometry_changed"


def http_client(live, tmp_path):
    from fastapi.testclient import TestClient
    from terra_monitoring.server import create_app

    return TestClient(create_app(service=live, web_root=tmp_path / "no-web-build"))


@pytest.mark.parametrize("measured", [False, True])
def test_http_actual_live_service_preserves_synthetic_flags_and_location_contract(tmp_path, measured):
    records = baseline_records()
    records[-5]["HlyAirTmp"]["Value"] = "120.0"
    live, _, clock = service(tmp_path, records=records)
    with http_client(live, tmp_path) as client:
        response = client.post("/api/assess", json=at_davis() if measured else dict(SITE))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    packet = response.json()
    assert packet["schema_version"] == "terra-location-assessment-v1"
    assert packet["data_mode"] == "synthetic"
    assert packet["location_estimate"]["status"] == "ok"
    assert 6 not in packet["location_estimate"]["used_station_ids"]
    assert len(finding(packet)) == 1
    assert finding(packet)[0]["reason"] == "extreme_departure"
    assert pd.Timestamp(packet["query"]["as_of_utc"]) == clock.value
    assert packet["provenance"]["live_source"]["status"] == "refreshed"
    comparison = packet["target_comparison"]
    if measured:
        assert comparison["classification"] == "suspected_anomaly"
        assert comparison["observed_f"] == 120.0
        assert comparison["departure_f"] == pytest.approx(120.0 - comparison["expected_f"])
        assert packet["provenance"]["peer_exclusion_basis"] == "target_held_out"
    else:
        assert comparison["reason"] == "no_independent_target_observation"
        assert comparison["observed_f"] is None and comparison["departure_f"] is None
    json.dumps(packet, allow_nan=False)


def test_http_actual_live_service_returns_null_estimate_outside_source_coverage(tmp_path):
    live, _, _ = service(tmp_path)
    with http_client(live, tmp_path) as client:
        response = client.post("/api/assess", json={"lat": 36.0, "lon": -120.0, "elevation_ft": 100.0})
    assert response.status_code == 200
    packet = response.json()
    assert packet["data_mode"] == "synthetic"
    assert packet["location_estimate"]["status"] == "insufficient_data"
    assert packet["location_estimate"]["temperature"] is None
    assert packet["target_comparison"]["observed_f"] is None
    assert packet["station_findings"] == []
    json.dumps(packet, allow_nan=False)


def test_http_actual_live_service_sanitizes_malformed_provider_response_as_503(tmp_path):
    live, source, _ = service(tmp_path)
    source.raw_body = "<html>PRIVATE-PROVIDER-DETAIL appKey=PRIVATE-KEY</html>"
    with http_client(live, tmp_path) as client:
        response = client.post("/api/assess", json=dict(SITE))
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "provider_invalid_response"
    assert set(response.json()["error"]) == {"code", "message", "retryable"}
    assert "PRIVATE-" not in response.text


def test_http_invalid_input_is_safe_422_before_actual_live_service_runs(tmp_path):
    live, source, _ = service(tmp_path)
    with http_client(live, tmp_path) as client:
        response = client.post("/api/assess", json={**SITE, "lat": "PRIVATE-INVALID-COORD"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_query"
    assert response.json()["error"]["retryable"] is False
    assert "PRIVATE-INVALID-COORD" not in response.text
    assert source.calls == []


@pytest.mark.parametrize("changed_field", ["lat", "lon", "elevation_ft"])
def test_http_sensor_geometry_mismatch_is_rejected_before_hourly_fetch(tmp_path, changed_field):
    live, source, _ = service(tmp_path)
    query = at_davis()
    query[changed_field] += 0.01
    with http_client(live, tmp_path) as client:
        response = client.post("/api/assess", json=query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_query"
    assert response.json()["error"]["retryable"] is False
    assert any(call[0] == "stations" for call in source.calls)
    assert not any(call[0] == "hourly" for call in source.calls)


def test_service_restart_preserves_unchanged_revision_first_seen_and_immutable_snapshots(tmp_path):
    records = baseline_records()
    records[-5]["HlyAirTmp"]["Value"] = "120.0"
    first_service, source, clock = service(tmp_path, records=records)
    before = first_service.assess(dict(SITE))
    first_event = finding(before)[0]
    first_digest = before["provenance"]["data_snapshot_id"].removeprefix("sha256:")
    snapshot_root = tmp_path / "live" / "snapshots"
    first_path = snapshot_root / f"{first_digest}.json"
    first_bytes = first_path.read_bytes()
    assert hashlib.sha256(first_bytes).hexdigest() == first_digest
    assert pd.Timestamp(first_event["visible_at_utc"]) == clock.value

    clock.advance(seconds=30)
    restarted = LiveQueryService(app_key="", source=source, data_dir=tmp_path / "live",
                                 clock=clock, ttl_seconds=120)
    after = restarted.assess(dict(SITE))
    second_event = finding(after)[0]
    second_digest = after["provenance"]["data_snapshot_id"].removeprefix("sha256:")
    second_path = snapshot_root / f"{second_digest}.json"
    assert after["data_mode"] == "synthetic"
    assert after["provenance"]["live_source"]["status"] == "refreshed"
    assert pd.Timestamp(after["provenance"]["live_source"]["retrieved_at_utc"]) == clock.value
    assert second_event["visible_at_utc"] == first_event["visible_at_utc"]
    assert second_event["detected_at_utc"] == first_event["detected_at_utc"]
    assert second_digest != first_digest  # Acquisition time identifies this saved snapshot.
    assert first_path.read_bytes() == first_bytes
    assert hashlib.sha256(second_path.read_bytes()).hexdigest() == second_digest
    assert len(list(snapshot_root.glob("*.json"))) == 2


def test_failed_refresh_provenance_persists_on_ordinary_cache_hit_until_recovery(tmp_path):
    live, source, clock = service(tmp_path)
    first = live.assess(dict(SITE))
    retrieved_at = first["provenance"]["live_source"]["retrieved_at_utc"]
    source.hourly_error = RuntimeError("controlled transient acquisition failure")
    clock.advance(seconds=30)
    failed = live.assess({**SITE, "refresh": True})
    failure_meta = failed["provenance"]["live_source"]
    assert failure_meta["status"] == "cached_after_error"
    assert failure_meta["warning"]
    assert failure_meta["failed_station_ids"] == failure_meta["station_ids_requested"]
    assert pd.Timestamp(failure_meta["last_attempt_at_utc"]) == clock.value
    calls_after_failure = list(source.calls)

    clock.advance(seconds=1)
    cached = live.assess(dict(SITE))
    cached_meta = cached["provenance"]["live_source"]
    assert source.calls == calls_after_failure
    assert cached_meta["status"] == "cached_after_error"
    assert cached_meta["warning"] == failure_meta["warning"]
    assert cached_meta["failed_station_ids"] == failure_meta["failed_station_ids"]
    assert cached_meta["retrieved_at_utc"] == retrieved_at
    assert cached_meta["last_attempt_at_utc"] == failure_meta["last_attempt_at_utc"]
    assert pd.Timestamp(cached_meta["checked_at_utc"]) == clock.value
    assert cached_meta["cache_age_seconds"] == pytest.approx(31)
    assert cached["provenance"]["data_snapshot_id"] == first["provenance"]["data_snapshot_id"]

    source.hourly_error = None
    clock.advance(seconds=1)
    recovered = live.assess({**SITE, "refresh": True})
    recovery_meta = recovered["provenance"]["live_source"]
    assert recovery_meta["status"] == "refreshed"
    assert recovery_meta["warning"] is None
    assert recovery_meta["failed_station_ids"] == []
    assert pd.Timestamp(recovery_meta["retrieved_at_utc"]) == clock.value
    assert pd.Timestamp(recovery_meta["last_attempt_at_utc"]) == clock.value


def test_last_attempt_is_owned_by_candidate_cohort_not_other_location_queries(tmp_path):
    live, source, clock = service(tmp_path)
    extra = source.stations.iloc[[0]].copy()
    extra.loc[:, "source_station_id"] = "999"
    extra.loc[:, "name"] = "Controlled extra source"
    extra.loc[:, "lat"] = 38.61
    extra.loc[:, "lon"] = -121.84
    extra.loc[:, "elevation_ft"] = 70.0
    source.stations = pd.concat([source.stations, extra], ignore_index=True)
    source.records.extend(raw_record(999, stamp) for stamp in HOURS)
    first_a = live.assess(dict(SITE))
    a_meta = first_a["provenance"]["live_source"]
    assert 999 in a_meta["station_ids_requested"]
    assert pd.Timestamp(a_meta["last_attempt_at_utc"]) == clock.value

    clock.advance(seconds=30)
    cohort_b = live.assess({"lat": 36.0, "lon": -120.0, "elevation_ft": 100.0})
    b_meta = cohort_b["provenance"]["live_source"]
    assert 999 not in b_meta["station_ids_requested"]
    assert b_meta["station_ids_requested"] != a_meta["station_ids_requested"]
    assert pd.Timestamp(b_meta["last_attempt_at_utc"]) == clock.value
    assert b_meta["last_attempt_at_utc"] != a_meta["last_attempt_at_utc"]
    calls_after_b = list(source.calls)

    clock.advance(seconds=1)
    second_a = live.assess(dict(SITE))
    a_cached = second_a["provenance"]["live_source"]
    assert source.calls == calls_after_b
    assert a_cached["status"] == "cached"
    assert a_cached["last_attempt_at_utc"] == a_meta["last_attempt_at_utc"]
    assert a_cached["retrieved_at_utc"] == a_meta["retrieved_at_utc"]
    assert pd.Timestamp(a_cached["checked_at_utc"]) == clock.value


def test_expired_cached_outage_has_cooldown_without_inventing_retrieval_or_retry_time(tmp_path):
    live, source, clock = service(tmp_path)
    first = live.assess(SITE)
    retrieved = first["provenance"]["live_source"]["retrieved_at_utc"]
    clock.advance(seconds=121)
    source.hourly_error = RuntimeError("controlled provider outage")
    failed = live.assess(SITE)
    attempted = failed["provenance"]["live_source"]["last_attempt_at_utc"]
    for _ in range(3):
        clock.advance(seconds=10)
        retained = live.assess(SITE)["provenance"]["live_source"]
        assert retained["status"] == "cached_after_error"
        assert retained["retrieved_at_utc"] == retrieved and retained["last_attempt_at_utc"] == attempted
        assert retained["cache_age_seconds"] > 121
    assert sum(call[0] == "hourly" for call in source.calls) == 2
    clock.advance(seconds=90)
    live.assess(SITE)
    assert sum(call[0] == "hourly" for call in source.calls) == 3
    source.hourly_error = None
    forced = live.assess({**SITE, "refresh": True})["provenance"]["live_source"]
    assert forced["status"] == "refreshed" and forced["warning"] is None
    assert sum(call[0] == "hourly" for call in source.calls) == 4


def test_initial_acquisition_failure_is_cooled_down_and_explicit_refresh_can_retry(tmp_path):
    live, source, clock = service(tmp_path)
    source.hourly_error = RuntimeError("controlled initial outage")
    for _ in range(3):
        with pytest.raises(LiveQueryError) as error:
            live.assess(SITE)
        assert error.value.code == "provider_unavailable"
    assert sum(call[0] == "hourly" for call in source.calls) == 1
    source.hourly_error = None
    assert live.assess({**SITE, "refresh": True})["provenance"]["live_source"]["status"] == "refreshed"
    assert sum(call[0] == "hourly" for call in source.calls) == 2


def test_failure_cooldown_does_not_mask_malformed_data_with_cached_success(tmp_path):
    live, source, clock = service(tmp_path)
    live.assess(SITE)
    source.payload_override = {"Data": {"Providers": "invalid"}}
    clock.advance(seconds=10)
    with pytest.raises(LiveQueryError) as error:
        live.assess({**SITE, "refresh": True})
    assert error.value.code == "provider_invalid_response"
    calls = list(source.calls)
    with pytest.raises(LiveQueryError) as error:
        live.assess(SITE)
    assert error.value.code == "provider_invalid_response" and source.calls == calls
    source.payload_override = MISSING
    assert live.assess({**SITE, "refresh": True})["provenance"]["live_source"]["status"] == "refreshed"


def test_current_cohort_cache_is_lru_bounded_and_keeps_durable_evidence(tmp_path, monkeypatch):
    live, source, clock = service(tmp_path)
    live.max_cache_entries = 2
    monkeypatch.setattr(live, "_candidates", lambda query, catalog, now: [6, int(query["location_id"])])
    def query(station):
        return {**SITE, "location_id": str(station)}
    live.assess(query(226))
    live.assess(query(139))
    live.assess(query(226))  # Reuse makes 139 the least recently used cohort.
    live.assess(query(212))
    assert len(live._cohorts) == 2
    assert sum(call[0] == "hourly" for call in source.calls) == 3
    live.assess(query(139))
    assert len(live._cohorts) == 2 and sum(call[0] == "hourly" for call in source.calls) == 4
    assert len(list((tmp_path / "live" / "snapshots").glob("*.json"))) >= 3
    assert (tmp_path / "live" / "first-seen.json").exists()


def test_previous_day_cohorts_are_evicted_without_deleting_snapshots(tmp_path):
    live, source, clock = service(tmp_path)
    for day in range(5):
        live.assess(SITE)
        assert len(live._cohorts) == 1
        clock.advance(days=1)
    assert len(list((tmp_path / "live" / "snapshots").glob("*.json"))) == 5


@pytest.mark.parametrize("changes", [{"max_cache_entries": 0}, {"max_cache_entries": True},
                                      {"failure_cooldown_seconds": -1}, {"failure_cooldown_seconds": float("inf")}])
def test_invalid_cache_bounds_fail_before_acquisition(tmp_path, changes):
    source = SavedRawSource(tmp_path / "raw")
    with pytest.raises(ValueError):
        LiveQueryService(source=source, data_dir=tmp_path / "live", **changes)
    assert source.calls == []


def test_catalog_outage_has_the_same_cooldown_and_explicit_refresh_recovery(tmp_path):
    live, source, clock = service(tmp_path)
    source.catalog_error = RuntimeError("controlled catalog outage")
    for _ in range(3):
        with pytest.raises(LiveQueryError) as error:
            live.assess(SITE)
        assert error.value.code == "provider_unavailable"
    assert sum(call[0] == "stations" for call in source.calls) == 1
    clock.advance(seconds=120)
    with pytest.raises(LiveQueryError):
        live.assess(SITE)
    assert sum(call[0] == "stations" for call in source.calls) == 2
    source.catalog_error = None
    assert live.assess({**SITE, "refresh": True})["provenance"]["live_source"]["status"] == "refreshed"
    assert sum(call[0] == "stations" for call in source.calls) == 3


def test_catalog_auth_failure_never_falls_back_to_previously_cached_metadata(tmp_path):
    live, source, clock = service(tmp_path)
    live.assess(SITE)
    source.catalog_error = CimisAuthError("private authentication failure")
    with pytest.raises(LiveQueryError) as error:
        live.assess({**SITE, "refresh": True})
    assert error.value.code == "provider_auth"
    calls = list(source.calls)
    with pytest.raises(LiveQueryError) as error:
        live.assess(SITE)
    assert error.value.code == "provider_auth" and source.calls == calls
    source.catalog_error = None
    assert live.assess({**SITE, "refresh": True})["provenance"]["live_source"]["status"] == "refreshed"
