# Terra · Always-On Monitoring Agent

Status: target product spec. The current milestone connects the cleaned
statistical station detector to Nathan's existing location estimator, emits
strict JSON assessments and displays those facts in the web UI. On-demand live
CIMIS location queries now use that same path through a local API. Always-on
operation, recipient subscriptions and external delivery follow that reviewed
integration; they are not claimed by this checkpoint.
See [the detailed monitoring spec](docs/terra/always-on-monitoring.md) for behavior,
acceptance checks and open decisions. The frozen reference, source provenance and verification are recorded in
[the checkpoint](docs/terra/checkpoint.md).
Local setup, the live HTTP contract, source bounds and failure behavior are in
[live location queries](docs/terra/live-queries.md).

The live checkpoint accepts a coordinate/elevation or a matching measured-station
preset. The server fetches recent observations, supplies the actual clock and
returns the exact JSON displayed and downloaded by the UI. Provider query time,
retrieval time and observation time remain distinct. Unsupported coverage,
unverified inputs and stale data withhold conclusions. Saved examples and
controlled faults retain their explicit data modes.

## Purpose

Give growers and crop consultants useful, proactive warnings about meaningful
chill trends and unreliable station data. A grower should understand the
situation and suggested check from the message without opening a dashboard.
Texts are the primary intended channel; recipients explicitly choose SMS,
email or another supported channel.

## First integration milestone

ET selected these two meanings on October 23, 2026:

- At a coordinate without a sensor, return estimated local temperature and
  relevant station flags, contributing/excluded stations, time and availability.
  Do not invent an observed local value or a local sensor fault.
- Where an independent reading exists, show a measured comparison. The frozen
  five-station detector supplies calibrated decisions only for its supported
  stations. A new sensor can show a numeric departure, with classification
  explicitly withheld until its policy is validated.

Reuse `chillwatch.estimate.estimate_location` and the actual frozen e1 QC. Keep
Nathan's ingestion, model, detection and repair unchanged. A coordinate may be
queried anywhere; usable estimates require supported peers and validated months.
The first message is a JSON data packet. The UI renders that packet without
recomputing thresholds or adding a language-model explanation.

## Target monitoring behavior

- Run independently of browsers and dashboard sessions. Persist progress and
  pending notifications, resume after restart, prevent overlapping checks and
  expose worker health, last successful check and actual data-through time.
- Check weather at a cadence matched to verified provider publication; evaluate
  seasonal chill from completed days. Polling cadence and maximum alert latency
  remain deployment decisions, not properties established by the local demo.
- Use two alert categories: **chill trends** and **data anomalies / station
  health**. Keep severity and opening/escalation/recovery transitions separate
  from category.
- Preserve the selected statistical temperature reference and its evidence.
  A suspected anomaly requires one extreme eligible reading or five consecutive
  moderate eligible readings. Missing/unavailable hours reset persistence.
  Insufficient usable peers withhold the comparison.
- For chill, compare accumulated reported hours at the same seasonal cutoff with
  usable prior seasons. Preserve below-20th, above-80th and recovery transitions,
  at least five of ten reference seasons and 95% reported coverage. Withhold
  conclusions on incomplete data; retain totals and reliability warnings.
- Flag station absence, missing observations, stale data and provider failure
  distinctly. Distinguish one lagging station, whole-source stagnation and a
  stopped monitoring worker. Unavailable data must not imply healthy conditions.
- Send an episode opening, meaningful severity escalation and recovery once per
  subscribed recipient/channel. Continuing observations update the evidence.
  Persist delivery attempts, retry recoverable failures and reconcile uncertain
  outcomes. A saved finding or provider acceptance is not proof of delivery.
- Respect verified contact details, station/category subscriptions and channel
  opt-in. Unsubscribe suppresses pending and future messages for that channel.
  Another channel is used only when separately opted in.
- State station, observation time, measured value/unit, comparison and practical
  checking action. Say “suspected”; do not assert sensor failure or crop damage.
  Percentiles describe comparisons, not probabilities of failure.
- Keep current alerts separate from historical replay and catch-up. Expire or
  summarize old incidents; recheck relevance immediately before delivery.

## Scope and integration

ET owns ingestion, observed-station QC, chill evaluation and alert delivery.
Nathan owns interpolation. Agree a versioned boundary for station IDs, units,
timestamps, source QC, availability, provenance and model versions before
integration. Keep interpolated values distinct from measured peer evidence.

Temperature and chill form the first complete delivery path. The wind example
requires separate wind/gust ingestion, quality handling and variable-specific
validation before it can be claimed as working. A zero reading alone is not a
fault rule. Additional severity percentiles, reminders and quiet-hour routing
need an explicit policy; this checkpoint does not retune the frozen detector.

## Demo and completion

The service continues checking with every dashboard closed. An opted-in test
recipient receives a temperature alert; repeat checks and restart do not create
duplicate openings. Demonstrate missing/stale data, recovery, chill transitions,
send failure/retry, unsubscribe and withheld stale historical events.

Label delivery as real SMS, another real opted-in channel or simulation. A new
Twilio trial cannot be assumed to support custom Terra texts: current Twilio
docs restrict trials to verified recipients and predefined bodies. Verify the
actual account before promising phone delivery. A simulated demo does not
satisfy real SMS acceptance. Hosting, sender/account readiness and Nathan's
contract remain explicit implementation dependencies.

