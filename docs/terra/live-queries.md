# Live location queries

The local API connects bounded recent CIMIS observations to the existing
`terra-location-assessment-v1` JSON and web UI. It uses the selected frozen e1
statistical reference and Nathan's temperature/geometry estimator. It does not
train, repair observations, send messages or install a continuous schedule.
Nathan's separate ingestion/model/repair pipeline remains available unchanged.

## Run locally

Use Python 3.12. Install the tested minimal dependencies, set the server process
environment explicitly, and start the loopback service from the repository root:

```powershell
python -m pip install -r requirements-terra-dev.txt
$env:PYTHONPATH = "backend"
$env:CIMIS_APP_KEY = "<your CIMIS AppKey>"
python -m terra_monitoring.server --host 127.0.0.1 --port 8512
```

On macOS/Linux, use `export PYTHONPATH=backend` and
`export CIMIS_APP_KEY="<your CIMIS AppKey>"` before the same Python command.
The existing ChillWatch configuration also reads the repository's ignored `.env`
without overriding process environment values. Keep the key on the server; it is not
accepted from the browser or returned in API JSON. `CIMIS_API_KEY` remains a
server-side compatibility alias, with `CIMIS_APP_KEY` taking precedence.

To build and serve the web UI from the same API process:

```sh
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

Open <http://127.0.0.1:8512>. The server serves `web/dist/client` when its build
exists. A missing build produces an explicit preview-unavailable response; the
API endpoints remain usable. The CLI accepts loopback hosts only.

For frontend development, keep the API on port 8512 and run:

```sh
pnpm --dir web dev --host 127.0.0.1 --port 8510 --strictPort
```

Open <http://127.0.0.1:8510>. Vite proxies `/api` to
<http://127.0.0.1:8512>; keep the credential out of frontend environment variables.
Saved examples and user-opened JSON remain separate choices in the preview.
A missing key or failed provider request never silently becomes a demo example.

## HTTP contract

- `GET /api/context` returns `terra-live-context-v1`, a configured boolean,
  source name, five calibrated station presets and the six-hour age policy.
  It does not fetch weather.
- `POST /api/assess` accepts finite numeric `lat`, `lon`, `elevation_ft`, optional
  positive-integer `target_station_id`, optional nonblank `location_id`, and
  boolean `refresh` (default `false`). Extra fields are rejected. The server
  owns observations, clocks, snapshot identity, source selection and data mode.
- `GET /api/health` reports that the local HTTP process responds. It does not
  establish provider freshness or continuous monitoring health.

An uninstrumented-location query in PowerShell:

```powershell
$query = @{
  lat = 38.6
  lon = -121.8
  elevation_ft = 60
  refresh = $false
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8512/api/assess -ContentType application/json -Body $query
```

For a measured station, use its matching preset coordinates/elevation and add
`target_station_id`. Do not label a different physical point with an existing
station identity. Unknown-to-e1 measured stations can receive a numerical
comparison, but no calibrated anomaly classification. An uninstrumented point
receives an estimate and relevant source-station flags; it has no independent
local observation or invented local fault label.

The successful body is the exact assessment JSON, including
`provenance.live_source`. API responses use `Cache-Control: no-store`.
Errors have the form `{ "error": { "code": "...", "message": "...",
"retryable": false } }`. Invalid fields, malformed JSON and nonfinite numbers
produce a sanitized validation response without echoing input. Unknown API
paths return 404 rather than the web UI.

Relevant source error codes are `source_not_configured`, `provider_auth`,
`provider_unavailable`, `provider_invalid_response`, `no_hourly_observations`
and `source_geometry_changed`. Messages are public summaries, not upstream
exceptions or URLs. A changed calibrated station location needs review; it is
not silently assigned the old frozen calibration.

## What a live query fetches

Acquisition uses the new CIMIS API and requests air temperature only. It covers
three fixed-PST source dates: today and the preceding two days. Requests include
the frozen five stations plus nearby estimator candidates and an explicitly
selected measured target, capped at 12 station IDs. This supplies recent context
for the five-consecutive-reading policy without a historical backfill or refit.
The latest reported completed UTC hour is assessed against the actual server
clock after acquisition; a successful request does not make old readings fresh.

Raw metadata is verified before certifying observations: requested station ID,
hourly scope, source date/hour, units and original QC. CIMIS is fixed UTC-8 all
year; hour-ending `2400` is midnight ending the reported source date and rolls
to the next calendar day. No daylight-saving shift or FNRIC alignment offset is added. Preserve
raw values/units and missing or warned QC. Only eligible source readings can
enter the detector or estimate. Operating dates apply before QC and interpolation.

The estimator needs sufficient eligible nearby stations and currently retains
its validated-month gate. Coordinates outside supported coverage can return
`insufficient_data`; stale results remain labelled and may retain a numerical
estimate. Neither outcome is an all-clear. The automatic numerical fault policy
is still calibrated only for Davis (6), Woodland (226), Winters (139), Hastings
Tract East (212) and Verona (235). Additional stations can contribute to estimates
without acquiring that calibration.

## Cache, provenance and failure

The shared service fetches on demand. Repeated queries for the same candidate
set/source-date range reuse an in-process snapshot for 120 seconds; `refresh:
true` asks for a new check. Provider requests are serialized and throttled.
The station catalog has a separate one-hour in-process cache. No timer keeps
polling after a browser tab closes.

Immutable snapshots and the first-seen revision index are saved beneath the
ignored `.terra-local/live` directory. The response cache is per process;
restarting the process requires another acquisition. A revision's visibility
means first observed by Terra, not the provider's original publication time.
The packet's snapshot digest identifies the retained evidence.

`provenance.live_source` separates these fields:

| Field | Meaning |
| --- | --- |
| `status` | `refreshed`, `cached`, or `cached_after_error` |
| `checked_at_utc` | Server clock used for this assessment |
| `retrieved_at_utc` | Time the retained snapshot was acquired |
| `last_attempt_at_utc` | Last weather-snapshot acquisition attempt |
| `data_through_utc` | Latest completed observation hour selected for assessment |
| `per_station_data_through_utc` | Latest completed timestamp per requested station |
| `cache_age_seconds` | Age of the retained acquisition, distinct from observation age |
| `station_ids_requested` | IDs requested for this candidate set |
| `missing_station_ids` | Requested IDs without a completed observation row |
| `failed_station_ids` | IDs associated with a failed acquisition batch |
| `warning` | Public failure/cached-data explanation, or null |

When a recheck fails and a usable in-process snapshot exists, the service may
return it with `status: cached_after_error`, original times and a visible warning.
The warning persists on cache hits until that candidate set has a successful
acquisition. Each candidate set keeps its own last-attempt time.
Freshness is still evaluated against the current server clock. Without a usable
snapshot, return the explicit source error. Credential rejection, malformed
provider evidence and changed calibrated geometry are explicit errors rather
than silent cached fallbacks.
A station appearing in the response does not establish that its temperature is
usable; inspect its source quality, exclusions and data-health evidence.

## Verify

Pure-runtime checks avoid the native backend's model-context fixture:

```sh
python -m pytest --noconftest -o pythonpath=backend tests/test_terra_json.py tests/test_terra_qc_reference.py tests/test_estimate.py tests/test_terra_live.py tests/test_terra_server.py -q
python terra.py check --base origin/main --task terra-live-location-queries
pnpm --dir web build
pnpm --dir web test:sites
```

The scoped check depends on the current task/base recorded in `COORDINATION.md`.
The full backend CI job separately installs `.[dev]` plus the pinned Terra
requirements, runs migrations and exercises the complete PostgreSQL/backend
suite. The minimal Windows/macOS jobs run the pure statistical, estimator,
source and HTTP path without installing ML. A manual workflow run repeats those
checks even when there is no source diff.

Use controlled source responses to verify extreme and sustained faults, gaps,
source warnings, revisions, operating dates, target holdout, unavailable peers,
stale source data, retries and safe HTTP errors. Keep controlled injections
labelled. A bounded actual-provider check must record its snapshot identity,
retrieval and data-through times, locations queried and observed availability.
A successful live fetch does not establish natural sensor-fault accuracy.

## Later deployment

This milestone is a local, on-demand JSON/UI service. External hosting needs an
explicit operator, server-side secrets, persistent snapshot storage, restart and
health supervision, provider-rate control and an authenticated/TLS boundary if
made reachable beyond loopback. A continuous monitoring worker, subscriptions,
alert lifecycle/delivery queue and SMS are separate implementation work. No
hosting, scheduled job, grower message or deployment is created by these commands.
