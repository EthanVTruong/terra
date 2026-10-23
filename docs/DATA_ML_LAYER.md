# ChillWatch data / physics / ML / detection layer

Python package `chillwatch` under `backend/`. It ingests station data, checks it, repairs it and
estimates temperatures where no station exists. It never sends messages and never changes issue
states: it returns `Detection` objects and writes reading-level status and estimates.

## Setup

```bash
uv venv -p 3.12 .venv && . .venv/bin/activate
uv pip install -e ".[dev]"            # add ".[prism]" for PRISM features (rasterio)
export CHILLWATCH_DB_URL=postgresql+psycopg://chillwatch:chillwatch@localhost:5432/chillwatch
alembic upgrade head                  # stations, readings, station_offsets, model_registry
export CIMIS_APP_KEY=...              # free key from et.water.ca.gov
```

Without `CHILLWATCH_DB_URL` the package uses an in-memory store loaded from
`data/parquet/{readings,stations}_{region}.parquet`.

## Pipeline (P0 order)

```bash
chillwatch stations sync --region config/region.yaml   # region = CIMIS stations within 150 km of the seeds + Durham
chillwatch backfill --from 2005-10-01 --to 2026-03-31   # resumable; raw JSON cached in data/raw/cimis/
chillwatch train                                        # LOSO metrics -> data/models/{version}/metrics.json
chillwatch bench --faults 100 --seed 42                 # -> bench/results/{timestamp}.json + .md
chillwatch scenario                                     # demo replay of config/scenarios/demo_2025_26.yaml
chillwatch blind-test 6 2024-25                         # hide a station, predict it from neighbors
chillwatch durham-rescue                                # LOSO on Durham's pre-closure seasons
```

Daily job (scheduler): `chillwatch ingest --days 3 && chillwatch daily`.

Offline dry run without a CIMIS key (SYNTHETIC data, never present the numbers):
`chillwatch synthetic-demo`.

## API for the rest of the system

```python
from chillwatch.qc.detect import run_detection, Detection
from chillwatch.qc.model import predict_expected, predict_at_location, set_offset_from_window
from chillwatch.repair.fill import fill_store
from chillwatch.repair.uncertainty import bootstrap_traces
from chillwatch.repair.virtual import blind_test, virtual_station
from chillwatch.demo.faults import apply_scenario

dets: list[Detection] = run_detection(as_of, lookback_days=7)   # also sets readings.qc_status
df = predict_expected(station_id, start, end)                   # ts, expected_f, est_sd, p10, p90, n_neighbors, method
df = predict_at_location(lat, lon, elevation_ft, start, end)    # virtual station
set_offset_from_window(station_id, start, end)                  # grower confirmed "it's on a hill"
traces = bootstrap_traces(station_id, "2024-25", n=200)         # error bars for chill totals
```

`Detection`: `station_id` (0 = system outage), `type` in
`offline | system_outage | range | step | stuck | spike | drift | offset | clock_shift`,
`severity` in `info | warn | critical`, `start_ts`, `end_ts` (None = ongoing), `hours_affected`,
`evidence` (dict), `suggested_reading_status` (`suspect | invalid | None`).
Same stored data → same detections. Match ongoing drift/offset detections by
(station, type, overlap): their onset estimate is refined as data arrives.

`readings` columns written here: `qc_base` (source QC), `qc_status` (final),
`est_temp_f`, `est_sd`, `used_temp_f`, `is_estimated`, `est_method` (`model | idw_lapse | none`).
Use `used_temp_f` for chill/GDD; NaN means no observation and no estimate (mark the day incomplete).

## Layout

```
backend/chillwatch/
  sources/{base,cimis,diy,prism}.py   adapters -> canonical schemas
  normalize.py                        PST hour-ending -> UTC, units, sub-hourly, dedupe, explicit gaps
  store.py, db.py, ingest.py          Postgres / in-memory stores, Alembic models, backfill / daily ingest
  qc/neighbors.py                     neighbor graph, hourly Theil-Sen lapse rate, IDW + lapse baseline
  qc/features.py, qc/model.py         LightGBM neighbor model, LOSO validation, offsets, inference
  qc/rules.py, qc/detect.py           rules + residual tests -> Detection
  repair/{fill,uncertainty,virtual}.py gap filling, empirical uncertainty, virtual stations, blind tests
  demo/{faults,scenario,synthetic}.py fault injection, scenario replay, synthetic network for tests
  bench/run.py                        fault-injection benchmark
  cli.py
config/{region,thresholds,cimis_flags}.yaml, config/scenarios/
docs/DECISIONS.md                     resolved / open VERIFY items and every constant not in the spec
```
