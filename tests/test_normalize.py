from datetime import date

import numpy as np
import pandas as pd

from chillwatch.normalize import (
    cimis_ts_utc,
    complete_hours,
    dedupe,
    local_date_of_hour,
    parse_local_timestamps,
    pst_day_hours,
    qc_status_from_source,
    to_fahrenheit,
    to_hourly,
)
from conftest import utc


def test_cimis_pst_to_utc_hour_ending():
    ts = cimis_ts_utc(["2024-01-01", "2024-07-01"], ["0100", "0100"])
    # PST all year: +8 h even in July (no daylight saving)
    assert ts[0] == utc("2024-01-01 09:00")
    assert ts[1] == utc("2024-07-01 09:00")


def test_cimis_2400_rolls_to_next_day():
    ts = cimis_ts_utc(["2024-01-31"], ["2400"])
    assert ts[0] == utc("2024-02-01 08:00")
    # and it belongs to Jan 31 local
    assert local_date_of_hour(ts)[0] == date(2024, 1, 31)


def test_pst_day_hours():
    h = pst_day_hours(date(2024, 1, 1), date(2024, 1, 2))
    assert len(h) == 48
    assert h[0] == utc("2024-01-01 09:00") and h[-1] == utc("2024-01-03 08:00")


def test_diy_offsets_and_dst():
    s = parse_local_timestamps(
        ["2025-03-09T01:30:00-08:00", "2025-03-09 03:30", "2025-11-02 01:30", "2025-07-01 12:00", "bad"],
        "America/Los_Angeles",
    )
    assert s[0] == utc("2025-03-09 09:30")
    assert s[1] == utc("2025-03-09 10:30")  # PDT, UTC-7
    assert pd.isna(s[2])  # ambiguous fall-back hour rejected
    assert s[3] == utc("2025-07-01 19:00")
    assert pd.isna(s[4])


def test_diy_no_offset_no_tz_rejected():
    s = parse_local_timestamps(["2025-01-01 10:00", "2025-01-01T10:00:00Z"], None)
    assert pd.isna(s[0])
    assert s[1] == utc("2025-01-01 10:00")


def test_celsius_to_fahrenheit():
    out = to_fahrenheit(pd.Series([0.0, 100.0, 50.0, 1.0]), pd.Series(["C", "°C", "F", "K"]))
    assert out[0] == 32.0 and out[1] == 212.0 and out[2] == 50.0 and np.isnan(out[3])


def test_subhourly_to_hourly_coverage_and_hour_ending():
    ts = pd.date_range(utc("2025-01-01 10:15"), periods=8, freq="15min")  # 10:15 .. 12:00
    df = pd.DataFrame({"source": "diy", "source_station_id": "a", "ts": ts, "temp_f": np.arange(8.0)})
    df.loc[[4, 5], "temp_f"] = np.nan  # hour ending 12:00 has 11:15, 11:30 missing -> 2/4 < 75%
    h = to_hourly(df, ["temp_f"], min_coverage=0.75).set_index("ts")["temp_f"]
    assert h[utc("2025-01-01 11:00")] == np.mean([0, 1, 2, 3])  # 10:15..11:00 -> hour ending 11:00
    assert np.isnan(h[utc("2025-01-01 12:00")])


def test_explicit_missing_rows_and_dedupe():
    hours = pd.date_range(utc("2025-01-01 09:00"), periods=4, freq="h")
    df = pd.DataFrame({
        "station_id": [1, 1, 1],
        "ts": [hours[0], hours[2], hours[2]],
        "temp_f": [40.0, 41.0, 42.0],
        "ingested_at": [utc("2025-01-02"), utc("2025-01-02"), utc("2025-01-03")],
    })
    d = dedupe(df, ["station_id", "ts"])
    assert len(d) == 2 and d.loc[d["ts"] == hours[2], "temp_f"].item() == 42.0
    full = complete_hours(d, hours, keys=["station_id"])
    assert len(full) == 4
    assert (full.loc[full["temp_f"].isna(), "qc_status"] == "missing").all()


def test_qc_mapping():
    st = qc_status_from_source(pd.Series([" ", None, "M", "Y", "Z", "R"]), pd.Series([1.0, 1, 1, 1, 1, np.nan]))
    assert st.tolist() == ["ok", "ok", "invalid", "ok", "suspect", "missing"]
