# Orchard Chill Monitor

**ENG 3, UC Davis - Nathan Ward, Carlo Repetto, Ken Nakao**

A low-cost logger that hangs in an orchard tree and measures winter chill where the tree actually
is, instead of at the nearest weather station. An Arduino Uno reads a DHT22 inside a decorative
"monkey" radiation shield, shows temperature and chill on an LCD, and beeps when something is wrong.
This repository holds the firmware, the data-validation layer that checks every reading, and the
chill calculations (chill hours for the prototype, Dynamic Model Chill Portions for the final
version).

## How it works

```
DHT22 in monkey shield -> Arduino Uno (LCD + buzzer) -> USB serial -> laptop / Pi bridge (UTC stamp)
   -> validation (rules + coverage) -> cross-check against nearby CIMIS stations
      (physics estimate + ML estimate, weighted by their uncertainty) -> chill totals + report
```

* **On the tree**: one reading a minute. The LCD shows temperature, humidity and a running
  chill-hour count saved in EEPROM. Three short beeps every minute while the sensor fails to read,
  and one long beep when the chill target is reached.
* **On the computer**: every line is checksummed, time-stamped and checked (duplicates, reboots,
  lost samples, frozen or jumping values, out-of-range readings). Hours with too few good readings
  are marked. Each hour is compared with what the reference stations predict for the logger's spot,
  so a sensor that drifts or gets sun on it is caught. Missing or rejected hours are filled with that
  prediction, clearly labeled as estimates.
* **Result**: chill hours and Chill Portions with a p10-p90 range, the share of hours actually
  measured, and a list of every data gap, on one HTML page. Details: [docs/INTEROP.md](docs/INTEROP.md).

## Hardware

| Part | Uno pin |
| --- | --- |
| DHT22 data (10 kOhm pull-up from data to 5V) | D2 |
| DHT22 VCC / GND | 5V / GND |
| Buzzer + (other leg to GND) | D8 |
| I2C LCD 16x2 backpack: SDA / SCL / VCC / GND | A4 / A5 / 5V / GND |

Firmware: [firmware/chill_logger](firmware/chill_logger/README.md). Install the "DHT sensor
library" (Adafruit), "Adafruit Unified Sensor" and "LiquidCrystal I2C" libraries, set `NODE_ID`,
and upload.

## Quick start

Requires Python 3.12.

```sh
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,serial]"
chillwatch logger demo            # synthetic run, no hardware or API key: data/logger-demo/report.html
```

With the logger plugged in:

```sh
cp config/nodes.example.yaml config/nodes.yaml    # set the tree's lat/lon/elevation
chillwatch logger bridge --port /dev/ttyACM0 --out data/orchard-1.jsonl     # COM3 on Windows
chillwatch logger validate data/orchard-1.jsonl --node orchard-1 --html-out report.html
chillwatch logger chill data/orchard-1.jsonl --node orchard-1 --season 2026-27
chillwatch logger calibrate data/orchard-1.jsonl reference.csv --node orchard-1   # +-0.3 C test
```

The cross-check needs the reference stations: put a free CIMIS AppKey in `.env`
(`CIMIS_APP_KEY=...`), then run `chillwatch stations sync` and
`chillwatch backfill --from 2026-11-01 --to 2027-03-31` (`chillwatch ingest` keeps it current). Data
is kept as a Parquet snapshot in `data/parquet`; see [docs/DATA_ML_LAYER.md](docs/DATA_ML_LAYER.md). `--no-reference` validates and totals without it.

## Layout

| Path | What |
| --- | --- |
| `firmware/chill_logger/` | Uno sketch and a host-side test harness |
| `backend/chillwatch/interop/` | record contract, bridge, readers, validation rules, hourly coverage, physics/ML fusion, report, `logger` CLI |
| `backend/chillwatch/chill/` | chill hours, Dynamic Model, season summary with uncertainty |
| `backend/chillwatch/` (rest) | ChillWatch reference layer: CIMIS ingest, station QC, physics core, LightGBM ML core, uncertainty, repair |
| `config/` | thresholds, region, logger registry |
| `tests/` | `pytest`; `tests/test_interop_*.py` and `tests/test_chill_models.py` cover this project |

## Development

```sh
ruff check backend tests
pytest                                             # ~4 min; the firmware test needs g++
python terra.py check --base origin/main --task <task-id>
```

Read [AGENTS.md](AGENTS.md) and record work in [COORDINATION.md](COORDINATION.md) on a task branch.

## History and license

This repository began as **Terra** (Ethan Truong and Nathan Ward): a local orchard-weather assistant
with station QC, chat and SMS. The ENG 3 refactor keeps Terra's ChillWatch data and model layer as
the reference network and removes the web app, chat, SMS and PostgreSQL pieces. All of it is
preserved in Git history at `87ee26b`.

MIT licensed ([LICENSE](LICENSE)). Readings and estimates are measurements and models, not crop
advice.
