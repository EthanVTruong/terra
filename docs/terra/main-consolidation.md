# Main consolidation verification

Commit IDs in the original evidence predate the requested authorship correction.
See [the old/new commit mapping](authorship-map.json); the application trees are unchanged.

This checkpoint consolidates the implemented statistical QC, location-assessment
JSON and message preview with the existing ChillWatch backend. The integration
base is `229106c251cfa41993a747cde2a40f29e53ed60f` (Nathan's PR #2).
[PR #3](https://github.com/EthanVTruong/terra/pull/3) contains the combined work.

## Branch coverage and ownership

The isolated adapter snapshot `d10c53e` and UI snapshot `0091b78` were compared
with the integrated tree. Their intended functionality is present, with later
peak-time, file-error recovery and verification repairs. Merging those obsolete
snapshots would add no missing functionality. Their local refs remain available
for recovery. The original Terra checkout and its nine staged bootstrap entries
are preserved; this shared checkout is the integration workspace.

Nathan retains ingestion, storage, interpolation, model and repair ownership.
Changes to his existing implementation are a portable grid-export date label,
the previously undeclared `matplotlib` plotting dependency, and synthetic demo
dates aligned with the configured year-round season. The numeric algorithms and
configuration are unchanged. The pure Terra path still runs from
`requirements-terra.txt` without importing the separate ML pipeline.

## Repairs found by verification

- Calibrated target comparisons now carry their typical historical temperature
  range into the JSON and preview. Unknown sensors receive no invented range.
  The measured example is 61.0°F observed, 37.3°F expected, 35.1–41.5°F typical,
  and +23.7°F departure. This range is neither a forecast nor a guaranteed
  confidence interval.
- Invalid unknown-sensor values become unavailable/null rather than creating a
  nonnumeric packet that the UI cannot open. Raw caller data remains unchanged.
- The adapter applies inclusive station operating dates using the existing
  fixed-PST hour-ending day. Inactive observations cannot affect the calibrated
  expectation or location/lapse estimate. Exclusions and operation-gate version
  remain explicit. The standalone preexisting estimator still needs callers to
  enforce lapse-contributor operation dates; use the assessment adapter for this
  gate. Missing dates retain permissive eligibility.
- Windows grid export no longer uses the unsupported `%-d` directive. Fresh
  full-backend installs now include the plotting library the export requires.
- Synthetic demo/test observations use the configured season rather than an
  obsolete October start. The September-start fault scenario now has data at
  its injection dates. Its synthetic training fixture also covers September;
  the former October–December fixture was outside its seasonal training range.
  Seeds, fold count, fault sizes, detector settings and assertions remain unchanged.

## Reproducible checks

The lightweight check runs 64 estimator/adapter/reference cases and 31 portable
coordination-harness cases:

```powershell
python -m pip install -r requirements-terra-dev.txt
python terra.py check --base fc47bf301b87bed1d6f328b01adfac904dc229e3 --task terra-main-consolidation
```

For the complete existing backend, use a separate environment:

```powershell
python -m pip install -e '.[dev]'
$env:CHILLWATCH_LGB_THREADS = '2'
$env:CHILLWATCH_DB_URL = 'postgresql+psycopg://chillwatch:chillwatch@127.0.0.1:5432/chillwatch'
$env:CHILLWATCH_TEST_DB_URL = $env:CHILLWATCH_DB_URL
python -m alembic upgrade head
python -m pytest
```

The repository workflow runs the complete backend suite with PostgreSQL 16,
migrations, synthetic fitting/replay fixtures and a check that no tests skip.
Without a local database, two local SQL cases skip and must not be reported as
passes. Separate Windows/macOS jobs retain the dependency-light JSON checks.
The web job installs the frozen lockfile, builds production assets and runs four
packaging checks. The PR Actions results record the exact tested revision.

Browser verification covers measured and uninstrumented locations, unknown
sensors, insufficient/stale data, malformed files, the calibrated range,
mobile focus/overflow and exact downloaded JSON. External clipboard round-trip
remains unverified; the UI reports copy success, but the inspection API returned
an empty clipboard value.

## What this verification establishes

The tests establish software behavior and controlled-fault handling. They do not
establish natural sensor-fault accuracy or verify live provider credentials.
The committed examples are historical saved observations with labelled
injections. A live location API, continuous worker, subscriptions, external
delivery and new chill products remain future scope in `spec.md`.
