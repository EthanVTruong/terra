# Data → flag → message integration contract

This is the shared implementation boundary for the autonomous completion task.
It carries forward ET's accepted product decisions; it does not change the
frozen station detector or Nathan's interpolation calculations.

## Ownership and the execution path

1. The live-source service acquires bounded CIMIS observations and owns the
   clock, units, source quality, publication visibility and snapshot identity.
2. The assessment adapter runs the frozen station QC and location estimator.
   `POST /api/assess` stays read-only and never sends a message.
3. The single farm monitor persists observations, orchard state, logical events
   and local inbox messages. It decides which meaningful orchard changes warrant
   notification. A flagged source station can remain in Details when adequate
   independent evidence still supports the orchard estimate.
4. The monitor drains persisted, eligible notifications at startup and each
   tick. Local inbox publication is separate from external delivery.
5. The SMS dispatcher applies recipient configuration, opt-in, freshness,
   expiry, mode, rate and duplicate gates, then uses one durable attempt ledger
   and transport implementation. It returns a redacted receipt.
6. The UI presents the same facts, the monitoring/feed state and actual delivery
   state. A location query remains free of send side effects even when SMS is
   enabled. An explicitly labelled demo action can send a controlled injected
   finding to the configured opted-in test phone.

## Trusted notification envelope

The farm monitor supplies `terra-notification-v1` envelopes to
`dispatch_event(envelope)` (`dispatch_message` may be an alias):

| Field | Meaning |
| --- | --- |
| `event_id` | Stable combined local-message ID; used for recipient-scoped deduplication |
| `source_event_ids`, `episode_ids`, `orchard_id` | Persistent logical origin and episode relevance |
| `data_mode` | `current`, `replay` or `injected`; only current automatically dispatches |
| `kind`, `category`, `transition`, `severity` | Message purpose, source category and existing lifecycle/severity labels |
| `text` | Immutable concise factual message; no recipient/contact secrets |
| `created_at_utc`, `checked_at_utc` | Message creation and fresh pre-dispatch eligibility check |
| `observation_time_utc` | Evidence time; nullable for a current data-health incident |
| `expires_at_utc` | Explicit message expiry |
| `eligibility` | Fresh eligible boolean and reason after opt-in/episode checks |

The envelope is an internal boundary. Browser input cannot supply arbitrary SMS
text, recipients, evidence, clocks or eligibility. Pending-message retrieval
rechecks whether all included events remain relevant; expired or superseded
facts must not be sent as a fresh alert.

The transport's canonical receipt fields are `status`, `provider_message_id`,
`updated_at_utc` and `source_event_id`. `provider_accepted` prevents a second
send, but remains eligible for receipt reconciliation until a terminal outcome.
The farm store preserves these fields instead of calling acceptance delivery.

## HTTP and runtime assembly

Use the implemented orchard API without parallel `/api/farm` aliases:

- `GET/POST /api/orchards` lists or explicitly creates an immutable profile.
- `GET /api/orchards/{id}` returns monitoring/chill status.
- `GET /api/orchards/{id}/messages` returns `terra-farm-feed-v1`, persisted
  messages, redacted delivery states and monitor status.
- `GET /api/orchards/{id}/messages/{message_id}/details` retains exact compact
  facts, source events, full assessments and transport receipt evidence.
- `GET/PUT /api/orchards/{id}/sms-opt-in` retains explicit durable consent.
- `POST /api/orchards/{id}/questions` supports factual answers; answers are not
  automatically sent as proactive SMS messages.
- `GET /api/monitor/status` and protected `POST /api/monitor/check` expose worker
  health and an explicit cycle.

Mutating orchard routes share the SMS service's raising local-session guard.
Read-only queries never become sends. Install the orchard router before
catch-all/static routes. One combined runtime uses the existing farm `run_loop`,
one live service, one monitor and one SMS dispatcher. Imports and unit-test app
factories do not start polling. Resolve private environment files and farm/SMS
journals explicitly to absolute paths.

## Freshness, failures and receipts

- A current data-health warning may describe an old or absent observation. Its
  eligibility comes from a fresh monitor check and active incident. This does
  not make stale numerical temperature evidence eligible.
- Current daily chill evidence follows the latest completed-day cutoff and its
  own coverage rules, rather than an hourly temperature observation-age limit.
- The dispatcher requires a fresh eligibility check, explicit expiry and current
  mode for normal sends. Demo delivery is a separate deliberate action and says
  that the injected weather fault is simulated.
- Safe definitive rejection can retry with persisted bounded backoff. A timeout
  or other uncertain acceptance must reconcile first and never blindly resend.
- Queued, sending, provider-accepted, delivered, failed, uncertain, expired and
  suppressed remain distinct. Provider acceptance is not proof of phone delivery.
- Opt-out suppresses queued and future sends and survives restart. The transport
  keeps its recipient-scoped journal; the farm store owns event/inbox state.

## Accepted product policies

Orchard and historical chill use 32–45°F starting November 1. Reported FNRIC
metrics remain separately labelled in Details. A behind-pace warning requires
two consecutive completed days below the historical 20th percentile with
adequate matching-method coverage. Missing history/observations withhold a
conclusion rather than becoming zero chill. Proactive updates can arrive
anytime, but continuing conditions do not repeat openings. Questions can be
answered anytime. Deterministic factual messages work without an LLM; an
optional writer cannot create events or invent measurements/crop impacts.

The accepted interface remains a tight single incoming conversation with three
readings, a visible quiet unavailable state and supporting artifacts/evidence.
The selected logo is supplied by its owner; an unresolved branding choice does
not block the working pipeline.

## Readiness evidence required

Exercise the complete source → assessment → monitor/store → envelope → transport
→ receipt/feed flow, including extreme and sustained faults, healthy orchard
support with a suspect station, missing/stale source, chill persistence and
recovery, restart/duplicate input, opt-out, expiry, safe failure/retry and uncertain
acceptance. Verify browser desktop/mobile and exact evidence, plus full native/
PostgreSQL and dependency-light CI. Record real provider and phone-delivery
evidence separately from mock transport and controlled weather injections.
