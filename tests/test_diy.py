from datetime import date

import pandas as pd
import yaml

from chillwatch.sources.diy import DiySource
from conftest import utc


def test_diy_csv_and_json(tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text(yaml.safe_dump([{"id": "g1", "name": "Grower 1", "lat": 37.7, "lon": -121.0,
                                    "elevation_ft": 90, "tz": "America/Los_Angeles"}]))
    up = tmp_path / "uploads" / "g1"
    up.mkdir(parents=True)
    rows = ["timestamp,temp,unit,rh"]
    for m in range(0, 120, 15):  # 15-minute logger in local time without offset
        t = pd.Timestamp("2025-03-09 00:15") + pd.Timedelta(minutes=m)
        rows.append(f"{t:%Y-%m-%d %H:%M},10,C,80")
    (up / "a.csv").write_text("\n".join(rows))
    (up / "b.json").write_text('{"readings": [{"timestamp": "2025-03-10T12:30:00-07:00", "temp": 55, "unit": "F"}]}')
    src = DiySource(reg, tmp_path / "uploads")
    st = src.list_stations()
    assert st.iloc[0]["source_station_id"] == "g1"
    h = src.fetch_hourly("g1", date(2025, 3, 8), date(2025, 3, 11)).set_index("ts")
    assert h.loc[utc("2025-03-09 09:00"), "temp_f"] == 50.0  # 00:15-01:00 PST, 10 C
    assert h.loc[utc("2025-03-10 20:00"), "temp_f"] == 55.0  # single sample, hourly-cadence file
    assert src.rejected["g1"] == 1  # 02:00 on the spring-forward day does not exist locally
