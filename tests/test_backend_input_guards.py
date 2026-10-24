"""Offline native decoder regressions; no API, database, model or training needed."""

import pandas as pd
import pytest

from chillwatch.ingest import run_per_station
from chillwatch.normalize import finalize_hourly
from chillwatch.sources.cimis import parse_daily, parse_hourly


def _payload(*records):
    return {"Data": {"Providers": [{"Records": list(records)}]}}


def _hourly_record(hour="0100", value="10", unit="(C)", station="6"):
    return {"Station": station, "Date": "2026-01-11", "Hour": hour,
            "HlyAirTmp": {"Value": value, "Unit": unit, "Qc": " "},
            "HlyDewPnt": {"Value": value, "Unit": unit, "Qc": " "}}


@pytest.mark.parametrize("unit,value,expected", [
    ("(C)", "10", 50.0), ("C", "10", 50.0), ("°C", "10", 50.0),
    ("deg C", "10", 50.0), ("Celsius", "10", 50.0),
    ("(F)", "50", 50.0), ("F", "50", 50.0), ("°F", "50", 50.0),
    ("deg F", "50", 50.0), ("Fahrenheit", "50", 50.0),
])
def test_native_temperature_units_keep_celsius_and_fahrenheit_semantics(unit, value, expected):
    result = parse_hourly(_payload(_hourly_record(unit=unit, value=value)))
    assert result["temp_f"].item() == expected
    assert result["dewpoint_f"].item() == expected
    canonical = finalize_hourly(result, {("cimis", "6"): 6})
    assert canonical["qc_status"].item() == "ok"


@pytest.mark.parametrize("unit", ["K", "Kelvin", "Rankine", "C/F", "nonsenseC", "", None, 77])
def test_unknown_temperature_units_are_missing_in_native_pipeline(unit):
    result = parse_hourly(_payload(_hourly_record(unit=unit)))
    assert pd.isna(result["temp_f"].item())
    assert pd.isna(result["dewpoint_f"].item())
    canonical = finalize_hourly(result, {("cimis", "6"): 6})
    assert canonical["qc_base"].item() == "missing"
    assert canonical["qc_status"].item() == "missing"


def test_native_batch_keeps_valid_rows_and_marks_unknown_unit_row_unusable():
    result = parse_hourly(_payload(
        _hourly_record("0100", "10", "(C)"),
        _hourly_record("0200", "50", "K"),
        _hourly_record("2400", "50", "(F)"),
    ))
    assert result["temp_f"].iloc[[0, 2]].tolist() == [50.0, 50.0]
    assert pd.isna(result["temp_f"].iloc[1])
    assert result["ts"].iloc[2] == pd.Timestamp("2026-01-12T08:00Z")
    canonical = finalize_hourly(result, {("cimis", "6"): 6})
    assert canonical["qc_status"].tolist() == ["ok", "missing", "ok"]


def test_native_daily_temperature_units_use_same_missing_data_guard():
    result = parse_daily(_payload({
        "Station": "6", "Date": "2026-01-11",
        "DayAirTmpMax": {"Value": "30", "Unit": "K", "Qc": " "},
        "DayAirTmpMin": {"Value": "10", "Unit": "(C)", "Qc": " "},
    }, {
        "Station": "6", "Date": "2026-01-12",
        "DayAirTmpMax": {"Value": "60", "Unit": "(F)", "Qc": " "},
        "DayAirTmpMin": {"Value": "40", "Unit": "(F)", "Qc": " "},
    }))
    assert pd.isna(result["tmax_f"].iloc[0])
    assert result["tmin_f"].iloc[0] == 50.0
    assert result["tmax_f"].iloc[1] == 60.0
    assert result["tmin_f"].iloc[1] == 40.0


def test_invalid_native_hour_fails_station_without_stopping_valid_station(caplog):
    stations = pd.DataFrame({"station_id": [6, 7]})

    def parse_station(station):
        hour = "2500" if station["station_id"] == 6 else "2400"
        return parse_hourly(_payload(_hourly_record(hour=hour, station=str(station["station_id"]))))

    result = run_per_station(stations, parse_station)
    assert isinstance(result[6], ValueError)
    assert "0100 through 2400" in str(result[6])
    assert result[7]["ts"].item() == pd.Timestamp("2026-01-12T08:00Z")
    assert "station 6 failed" in caplog.text
