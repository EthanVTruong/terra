import numpy as np
import pandas as pd
import pytest

from chillwatch.sources.asos import FT_PER_M, parse_csv, parse_network
from conftest import utc

CSV = """station,valid,tmpf,dwpf,relh,sknt
SAC,2024-01-10 08:53,46.00,43.00,89.20,5.00
SAC,2024-01-10 09:20,45.00,43.00,92.00,4.00
SAC,2024-01-10 09:53,42.00,40.00,92.55,0.00
SAC,2024-01-10 10:53,,39.00,96.18,3.00
"""


def test_parse_csv_hour_ending_and_one_value_per_hour():
    df = parse_csv(CSV, "ref").set_index("ts")
    assert df.loc[utc("2024-01-10 09:00"), "temp_f"] == 46.0  # 08:53 -> hour ending 09:00
    assert df.loc[utc("2024-01-10 10:00"), "temp_f"] == 42.0  # latest obs in the hour wins (the :53 routine)
    assert np.isnan(df.loc[utc("2024-01-10 11:00"), "temp_f"])  # missing value stays NaN
    assert df["wind_mph"].iloc[0] == pytest.approx(5 * 1.150779448)
    assert set(df["source"]) == {"asos"}


def test_parse_network():
    payload = {"features": [
        {"id": "SAC", "properties": {"sname": "Sacramento", "elevation": 8.0, "archive_begin": "1931-01-01",
                                     "archive_end": None, "online": True, "county": "Sacramento"},
         "geometry": {"coordinates": [-121.495, 38.5069]}},
        {"id": "IYK", "properties": {"sname": "Inyokern", "elevation": 749.0, "archive_begin": "1973-06-21",
                                     "archive_end": "2013-11-04", "online": False},
         "geometry": {"coordinates": [-117.8, 35.65]}}]}
    st = parse_network(payload).set_index("source_station_id")
    assert st.at["SAC", "elevation_ft"] == pytest.approx(8.0 * FT_PER_M)
    assert st.at["SAC", "is_active"] and not st.at["IYK", "is_active"]
    assert st.at["IYK", "disconnect_date"] == pd.Timestamp("2013-11-04")
    assert st.at["SAC", "lat"] == pytest.approx(38.5069)
