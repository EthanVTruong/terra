# Terra · Always-On Monitoring Agent

The [Terra conversation contract](docs/terra/conversation-contract.md) records the
accepted grower voice, concise alerts and follow-ups, optional reactions/silence
and conversational setup requirements. Its integration status remains explicit.

## Current product

The consolidated application is a local, single-user orchard monitor with conversational crop/location setup, guarded live CIMIS queries, persistent notes and evidence, supported farm/weather follow-ups, optional browser notifications, optional ChatGPT writing and optional Twilio delivery. It uses the frozen temperature QC reference and Nathan's location estimator. Relative humidity, hourly average wind and solar have separate provisional station-peer rules; their findings do not change temperature QC or invent orchard measurements.

Start with [the README](README.md) and [local setup](docs/terra/setup-and-hosting.md). The default `python run_terra.py` launches a saved-example preview with providers and worker disabled. `--live` enables the five-minute monitor with the operator's own CIMIS configuration. Monitoring continues with the browser closed while the process and computer remain running. Browser notifications require an open Terra page. SMS requires a configured eligible sender/recipient and explicit orchard opt-in; ChatGPT is optional and has a factual fallback.

The [farm-chill rules](docs/terra/farm-chill-ops.md) are authoritative for the implemented orchard product: estimated 32–45°F hours over November–March, incomplete hours withheld from complete-data conclusions, and historical pace requiring at least five comparable, 95%-complete reference seasons. No such archive is automatically populated on a new install. Separately reported FNRIC station totals retain their own convention and cannot replace orchard history. The configured cultivar reference is not a validated biological threshold.

The [Tara workflow](docs/terra/tara-conversation.md), [weather contract](docs/terra/weather-contract.md), [runtime](docs/terra/local-runtime.md), [phone setup](docs/terra/twilio-sms.md) and [readiness record](docs/terra/readiness.md) describe the current boundaries. Saved-example, station-lab and production-interface replay are labelled historical/controlled data, with separate state and simulated phone cards. Live query, retrieval, first-seen visibility and observation clocks remain distinct; missing/stale/source-warning evidence withholds unsupported conclusions.

The sections below preserve the original product target and first-integration decisions. They are historical requirements, not a claim that email, arbitrary subscriptions, hosted operation or the original reported-chill policy are implemented. Current behavior is defined by the linked executable contracts and runbooks. The frozen reference and its verification are recorded in [the checkpoint](docs/terra/checkpoint.md).

## Original purpose

Give growers and crop consultants useful, proactive warnings about meaningful
chill trends and unreliable station data. A grower should understand the
situation and suggested check from the message without opening a dashboard.
Texts are the primary intended channel; recipients explicitly choose SMS,
email or another supported channel.

## First integration milestone (historical)

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

## Original target monitoring behavior (historical)

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

## Current scope and integration

ET owns ingestion, observed-station QC, chill evaluation and alert delivery.
Nathan owns interpolation. The integrated contracts retain station IDs, units, timestamps, source QC, availability, provenance and model versions. Keep interpolated values distinct from measured peer evidence.

Temperature and chill retain their existing delivery path. Independent CIMIS
relative humidity, hourly average wind and solar radiation now use explicit
units, per-variable source quality and provisional peer-comparison rules. Their
findings and data-health states remain separate from temperature QC and orchard
estimation. Zero calm wind and nighttime solar alone are not faults. Wind gusts,
crop-risk thresholds and field sensor-fault accuracy have not been established.
See [the weather contract](docs/terra/weather-contract.md) and
[simulation handoff](docs/terra/weather-simulation-handoff.md). Additional severity
percentiles, reminders and quiet-hour routing need an explicit policy; this
checkpoint does not retune the frozen temperature detector.

## Original demonstration acceptance (historical)

The service continues checking with every dashboard closed. An opted-in test
recipient receives a temperature alert; repeat checks and restart do not create
duplicate openings. Demonstrate missing/stale data, recovery, chill transitions,
send failure/retry, unsubscribe and withheld stale historical events.

Label delivery as real SMS, another real opted-in channel or simulation. A new
Twilio trial cannot be assumed to support custom Terra texts: current Twilio
docs restrict trials to verified recipients and predefined bodies. Verify the
actual account before promising phone delivery. A simulated demo does not
satisfy real SMS acceptance. Hosted multi-user operation remains deferred. Sender/account readiness and genuine phone delivery remain installation-specific acceptance; the numerical integration and local worker are implemented.

