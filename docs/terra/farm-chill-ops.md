# Farm-chill-ops

This is the persistent seasonal-watch layer over the existing live CIMIS query,
frozen e1 statistical QC and Nathan's location estimator. Those numerical
formulas and calibration are unchanged. The first profile is Nonpareil almond at a configured
Davis-area **demonstration** pin, not an independently verified real orchard.

The backend now saves orchard profiles, hourly estimates and revisions, full
evidence, incident state, logical events and immutable local inbox messages in
one SQLite database. A single worker resumes that state without a browser.
The optional SMS dispatcher is a separate injected transport; the farm module
does not create a second SMS ledger or scheduler.

## Product behavior

- Orchard chill is estimated from hourly temperatures between **32 and 45°F,
  inclusive, beginning November 1** and ending March 31. This matches Nathan's
  existing chill window. Calendar boundaries use CIMIS's fixed-PST hour-ending
  convention; midnight belongs to the preceding observation day.
- Missing hours are unknown, not zero chill. The displayed count retains known
  estimated chill and is explicitly incomplete below 95% coverage. No location
  measurement or uncertainty interval is invented.
- Historical comparisons use the same coordinate/elevation, estimator/QC
  contract and calendar cutoff. At least five of the preceding ten seasons
  need 95% coverage. February 29 remains in the displayed total and actual
  coverage but is excluded from all comparison totals. A different model or
  geometry cannot quietly supply a reference season.
- A pace warning requires two consecutive completed days below the historical
  20th-percentile value. Repeated checks of the same day cannot advance it.
  Recovery requires three valid completed-day comparisons out of that low range.
- Weather support becoming unusable creates one monitoring-limited opening.
  Continuing failure escalates once after six hours. Recovery requires three
  **distinct consecutive hourly** usable estimates; a gap resets the count.
  Six hours is the initial operational escalation/age default, not a new
  agronomic threshold or a change to e1's numerical detector.
- A flagged public station stays in Details while remaining sources support
  the orchard estimate. Source warnings are not confirmed hardware failures.
- One initial summary is followed by meaningful changes only. Related events
  are combined, most urgent first. There are **no quiet hours**. Questions can
  be answered anytime. Unchanged checks and restarts do not repeat openings.
- The 400-hour Nonpareil value is a **configured reference**, not a verified
  biological requirement: the [UC Davis cultivar page](https://fps.ucdavis.edu/treedetails.cfm?hidepics=yes&v=1064)
  does not establish equivalence to this exact counting convention. It can
  produce a once-per-season reference milestone with adequate coverage, but
  never a claim that dormancy ended or bloom is assured. See also
  [UC Davis's explanation of chill methods](https://fruitsandnuts.ucdavis.edu/about-chilling-hours-units-and-portions).
- Reported FNRIC station totals retain their September-start convention and
  appear only in a separately labelled Details record. They do not populate
  orchard totals, historical percentiles or crop milestones.

## Compact JSON and message writer

The executable message contract is produced by `writer.message_input`:
one orchard, a mode marker, and only the selected event facts. No raw hourly
history, neighbor arrays, dataset/model hashes, delivery contacts or previous
conversation are sent to the writer. Only relevant fields are present.

- [JSON schema](farm-chill-ops/message-input.schema.json)
- [Exact system prompt](farm-chill-ops/system-prompt.md)
- [Compact input example](farm-chill-ops/message-input.example.json)
- [Actual API feed fixture](farm-chill-ops/feed-fixture.json)

Python decides detection, severity, lifecycle, grouping, thresholds and delivery
eligibility. The optional writer generates concise prose for each selected event;
it does not choose from fixed messages. A strict `text.format` JSON schema requires
exact event keys (`event_0`, `event_1`, etc.), each containing a short clause.
Quantities use role-bound placeholders such as `{{hours}}` for current estimated
chill and `{{historical_median_hours}}` for the same-point historical median.
Python substitutes the exact values, preserves event order, and adds the orchard
name, original source times and demo marker. Final messages are at most 420
characters. Neighbor measurements and the full assessment remain under Details.

The model receives per-event categorical facts, placeholder values, the accepted
English vocabulary and local quantity-role hints, rather than complete candidates;
orchard names, free-text questions, raw weather history and source timestamps are
excluded from that request. The saved `llm_input` remains the original compact
factual packet. Local checks require every expected placeholder exactly once in
its factual role, reject unbound quantities/units, and check lifecycle, estimated
status, incomplete coverage and historical availability. A small English
vocabulary rejects unsupported actions, biological claims, causes, invented times
and unfamiliar wording. These are conservative semantic checks, not a proof of
truth for arbitrary natural language; valid but unfamiliar paraphrases may fall
back. New event explanations need an explicit supported contract before generation.

Without a configured generator, the OpenAI Responses path requires both
`OPENAI_API_KEY` and `TERRA_LLM_MODEL`. Alternatively an existing provider can be
injected as `Writer(generator=...)`; its `generate(instructions, packet, schema)`
returns `{"text": <JSON event clauses>}`. Both paths use the same local validation.
An injected generator failure goes directly to the local template and never
silently switches to API-key billing. This module does not create an account,
model, key or plan connection. The HTTP path sets `store: false` and keeps no
provider conversation. Provider failure, refusal, incomplete output, extra or
duplicate fields, invalid wording and oversized output all use the unchanged
deterministic fallback; ingestion and event publication remain available.
[Official Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Run

Install the existing `requirements-terra-dev.txt`. In PowerShell from this
checkout, set the import path and an **absolute shared state directory** once:

```powershell
$env:PYTHONPATH = 'backend'
$terraState = (Join-Path (Get-Location) '.terra-local\runtime')
python -m terra_monitoring.farm_ops --state-dir $terraState add-orchard --id west --name 'West Orchard'
python -m terra_monitoring.farm_ops --state-dir $terraState once
python -m terra_monitoring.farm_ops --state-dir $terraState run --interval 300
```

The existing live source reads its configured CIMIS key from process environment.
No key is printed. Without configured access, the local monitor reports limited
weather support and retains its cache. `run` is independent of browser sessions;
stop it with Ctrl+C and restart it against the same database. This hack demo
does not require an overnight host or operating-system service installation.
No schedule is installed in Codex.

The worker checks immediately on startup, then at the configured interval.
One durable lease prevents overlapping checks. Runtime heartbeat, last check,
actual data-through, incomplete coverage and pending events remain separate.
Source failure never replaces a retained good observation with a fabricated one.

For a completely offline source-to-message demonstration:

```powershell
python -m terra_monitoring.farm_ops demo
```

This uses a separate demo database, the committed historical weather request,
the real e1/Nathan assessment path and an explicit one-hour availability
injection. It opens and recovers one incident. Every message says **Demo replay**.
It does not fetch weather, train, call an LLM or send a text. Repeating the demo
reuses the saved result; choose another `--orchard-id` for an independent replay.

## HTTP assembly

`api.create_router(monitor, mutation_guard=shared_guard)` is the supported
assembly hook for the existing server. Include it before static/catchall routes.
The root runtime owns the single process/worker and the shared loopback
Host/Origin/session-token guard. Mutations default-deny without that raising
dependency. No scheduler starts from import, GET or `/api/assess`.

| Route | Meaning |
| --- | --- |
| `GET /api/orchards` | Saved profiles |
| `POST /api/orchards` | Save name/pin/elevation and the Nonpareil preset; optional explicit `sms_opt_in` before the first check |
| `GET /api/orchards/{id}` | Current seasonal summary, coverage, data-through and monitor state |
| `GET /api/orchards/{id}/messages` | `terra-farm-feed-v1`, newest-first persisted messages and redacted delivery state |
| `GET /api/orchards/{id}/messages/{message_id}/details` | Exact compact input, events, full assessments, archive provenance, separate FNRIC and receipt |
| `POST /api/orchards/{id}/questions` | `{question, request_id?}`; idempotent factual reply |
| `GET/PUT /api/orchards/{id}/sms-opt-in` | Durable explicit channel preference; PUT `{enabled:bool}` |
| `GET /api/monitor/status` | Worker heartbeat/check and transport readiness |
| `POST /api/monitor/check` | One explicit bounded worker cycle |

The input pin/elevation are caller-supplied geometry. Browser GPS collection,
pin confirmation and elevation acquisition are frontend/source responsibilities;
this backend does not claim to have measured the orchard terrain. The default
38.6,-121.84,70ft geometry is visibly a demo assumption. Profiles are immutable:
another location/method needs another ID so its history cannot mix silently.

Questions are restricted to chill totals, consistent historical comparisons,
supporting-station health and existing alerts. GDD/forecasts, bloom predictions
and treatment recommendations get an explicit unsupported/unavailable answer.
An answer is `kind=answer` and never enters the proactive SMS outbox.

## One outbox and one transport

`messages.status=published` means local inbox visibility, **not SMS delivery**.
Each combined message has an immutable ID/text and only its communicated
`event_ids`; obsolete processed events are not mixed into its identity. Events
also have stable `episode_id`, mode, lifecycle and severity.

The injected dispatcher implements `dispatch_event(terra-notification-v1)`.
`Monitor.notifications_pending()` rechecks mode, explicit opt-in, time, expiry,
current episode and seasonal-cutoff relevance. It never permits replay/injected
data or a pre-opt-in backlog through normal delivery. A current data-health
message can describe an old/absent observation; freshness comes from the active
incident and a fresh monitor check. Seasonal evidence is checked by its
completed-day/method policy, not by a misleading hourly age rule.

`Monitor.check()` always drains the persisted outbox after processing, including
when no new events appeared. Drain first calls `reconcile_pending(limit=10)`
and records results by `source_event_id`, then dispatches eligible immutable
messages. Crash between local publication and transport is recoverable.
`provider_accepted` stops resending but remains distinct from `delivered`.
Uncertain sends never blindly retry. The SMS owner's journal owns per-recipient
attempts/backoff and private sender/recipient configuration. No duplicate journal
or second scheduler exists in this module. SMS is opt-in and disabled by default.

## History and data readiness

The current live API supplies only a bounded recent snapshot. The worker
reconstructs at most three days using the **existing** assessment function;
it does not silently download a season or ten years. Existing trusted hourly
orchard-estimate archives can be loaded explicitly with `import-history`.
The archive must name its exact geometry/estimator/QC contract, mode and units;
duplicates, future hours and incompatible current histories fail atomically.
It is a trusted operator import, not a grower-upload substitute for verification.

The local original prototype has multi-year station weather and ten-year FNRIC
station chill, but those are not already a matching five-season orchard archive.
Without reconstructed orchard history, comparisons are withheld. Controlled
synthetic fixtures test the five-season percentile/milestone behavior; that does
not establish natural fault accuracy, biological targets or orchard microclimate
accuracy. Existing model-validation limitations still apply.

`import-fnric-details` accepts existing reported daily station records separately.
It never changes the orchard series or reference gate.

## Verification

```powershell
python -B -m pytest --noconftest -o pythonpath=backend -p no:cacheprovider tests/test_farm_chill_ops.py tests/test_farm_chill_ops_api.py -q
python terra.py check --base 7f37e2670103fc2efa83c272e1cab285e3616620 --task farm-chill-ops
```

Tests cover seasonal boundaries/leap days, missing and incompatible history,
two completed days, three distinct recovery readings, milestone deduplication,
source failures/corrections, restarts, leases, combined messages, writer fallback,
API guards, question idempotency, opt-in/out, immutable outbox restart recovery,
uncertain transport and acceptance-versus-delivery reconciliation. The saved
weather demo exercises the actual source-assessment boundary rather than
scripting a model decision.
