# Always-on monitoring · behavior and acceptance

Status: proposed implementation contract, expanded from ET's Always-On
Monitoring Agent brief on October 23, 2026. This document specifies the next
product phase; it does not claim that a background service or SMS delivery
already exists. Current software evidence is recorded separately in [checkpoint.md](checkpoint.md).
The first reviewed milestone is JSON location assessments and UI rendering;
the service and external-delivery requirements below remain a subsequent phase.

## What the cleaned checkpoint provides

- Real-source ingestion and local SQLite persistence, revisions and quarantine.
- Frozen statistical temperature QC, deterministic evidence and causal replay.
- Seasonal chill summaries and entry/recovery transitions.
- Stored source-health findings and local restart deduplication.
- A Streamlit local application and a React snapshot message preview.

It does not provide browser-independent monitoring, a live message transport,
subscriptions, delivery receipts or wind anomaly detection. The React preview
consumes a generated snapshot; it is not the monitoring worker.

## One complete service cycle

Read verified provider observations -> retain originals and revisions -> assess
availability -> evaluate temperature, chill and source health -> persist events
and pending deliveries -> apply recipient preferences and freshness -> send
through the selected channel -> record delivery outcome.

Use a single worker and persistent storage initially. A browser is an optional
viewer. The worker must recover from restart, retain its progress and pending
work, and prevent overlapping jobs from generating duplicate notifications.
Keep live, replay and injected events in separate namespaces; demo injections
must never enter real recipient delivery accidentally.

Weather checks follow verified source publication behavior; chill evaluation
uses completed days and a consistent seasonal cutoff. Configure source retry
and polling intervals explicitly. Repeated polling does not make old source
observations current. Record worker heartbeat, attempt time, successful check,
data-through time and oldest pending delivery separately. A mechanism outside
the worker must be able to detect a stopped worker. Route worker/delivery
failures to the operator; growers receive subscribed data-availability effects.

## Event categories, severity and evidence

| Category | Events | Initial policy |
| --- | --- | --- |
| Chill trends | Enter low/high historical range; return to typical | Existing below-20th, above-80th and recovery transitions |
| Data anomalies / station health | Temperature departure, absent/missing/stale observations, unavailable comparison, provider failure/recovery | Existing numerical QC and source-health logic, plus an explicit live freshness/health policy |

Category describes what happened; severity describes the required attention.
Lifecycle transition is opening, escalation, update or recovery. The proposed first routing policy maps sustained temperature departures to
warning and extreme departures to urgent inspection; it does not imply confirmed
failure or crop harm. Chill percentile entries are trend advisories. Data-health
severity follows the agreed duration/impact policy. ET must select quiet-hour
and warning/urgent routing before activation. A high seasonal chill percentile
does not establish crop harm or automatically justify urgency. Additional
percentile bands need a documented agronomic rationale and approval.

Retain statistical reference `terra-qc-v4-e1e59ac0ea395d80` and its selected
five-moderate/one-extreme temperature policy. Missing/unavailable hours reset
persistence. The target reading cannot contribute to its own expected value.
Preserve simultaneous eligible measured peers and the existing geometry/support
gates. Do not replace missing neighbors with interpolated evidence.

Chill keeps at least five usable references among the previous ten completed
seasons and 95% reported hourly coverage. Use the same seasonal cutoff and
retain the established leap-day treatment. Missing coverage withholds the
conclusion and does not cause a false recovery. Retain reported totals while
attaching source and suspected-temperature reliability warnings.

Each event carries a stable ID, schema version, category/type, station,
variable/unit, severity, lifecycle transition, episode ID, observation and
detection times, data-through time, freshness/expiry, measured and comparison
values, peer evidence, source QC/availability, reference/policy versions and
live/replay/injected provenance. Unavailable numeric values remain unavailable;
do not invent them. Keep peak evidence time distinct from the trigger time.

## Availability and current alert eligibility

The current `monitor_current()` retains retrospective QC findings and separately
reports current availability. Its saved catch-up summary is not a ready-to-send
live alert. A delivery policy must evaluate the event's observation age, whether
the incident remains active and whether its evidence is still assessable.

Distinguish these cases:

- A station lags peers or is absent.
- Source timestamps exist but readings are missing, flagged or unverified.
- All stations are equally old, so relative lag alone cannot reveal stagnation.
- The provider request fails while a cache remains readable.
- The monitoring worker stops and no checks occur.

The existing six-hour current comparison gate, six-hour relative station lag
and 24-hour unchanged-source check are baseline behavior, not an approved
latency target for proactive alerts. Operational thresholds must be explicit,
source-aware and agreed before the live demonstration. When observation age
exceeds policy, send the relevant data-health event rather than implying the
old temperature departure is happening now.

Historical incidents that have recovered belong in a labelled catch-up summary
when useful. Re-evaluate queued deliveries after delay or restart; expire,
suppress or summarize events that are no longer relevant. Quiet unavailable
status in the preview and an opted-in data-health notification can coexist:
missing evidence does not become a numerical temperature anomaly.

## Durable notification lifecycle

Persist an event and its intended deliveries in one transaction. A unique
delivery identity includes episode/event, recipient, channel and transition or
severity change. Existing finding persistence is separate from delivery state.

Track queued, sending, provider-accepted, delivered, failed, expired, suppressed
and uncertain outcomes, with attempt time, provider message ID and sanitized
failure details. Accept signed/validated provider receipts where supported;
otherwise state the weaker available evidence. Provider acceptance does not
mean a phone received the message.

Retry transient errors with bounded backoff and preserve pending work through
restart. After a timeout that may have succeeded, reconcile provider status
before issuing another send. Do not promise unconditional exactly-once carrier
delivery. Use local uniqueness and provider reconciliation to prevent avoidable
duplicates.

Send one opening per subscribed episode, one meaningful escalation and one
recovery when established. Ongoing hourly readings update evidence. Automated
reminders and cooldown rules remain disabled until a policy is selected.
A recovery from missing data is distinct from evidence that a suspicious sensor
has been repaired. Uncertain or unavailable conditions do not prove recovery.

## Subscription and message behavior

Store verified contact/channel, selected stations, enabled categories,
severity preferences, timezone and consent history. SMS is the intended primary
channel. Email/other channels are explicit choices. Apply unsubscribe/STOP
before both enqueue and send, suppress queued deliveries and retain the opt-out
record across restart. Channel fallback requires separate opt-in; do not send
to an unselected channel because SMS failed.

A message must stand on its own: station, variable, observation time, measured
value/unit, relevant comparison and a practical check. Evidence links provide
optional detail. Use concise deterministic templates initially. No LLM is
required to decide whether an alert exists or to send one.

Examples below are illustrative, not findings from the current data:

- Temperature: “Station X: 40°C at 10 AM, versus 20°C across 3 usable nearby
  stations. Suspected unusual reading; check an independent thermometer.”
- Missing data: “Station X has no new usable reading since 8 AM. Check the
  station connection; current temperature comparison is unavailable.”
- Chill: “Station X's accumulated chill is below the historical 20th percentile
  through yesterday. Reported total: [hours]. [Coverage warning if present.]”
- Wind, after validation: “Station X reports 0 knots; nearby stations report
  18 knots with gusts to 25. Check the wind sensor and local conditions.”

Use displayed units consistently; a temperature difference converts with the
scale factor only. Do not describe adjusted expected temperature as a raw
neighbor median or say “all nearby stations” when only selected peers were used.
Source warnings are not confirmed faults. Percentiles are not failure
probabilities. Do not invent forecast, cause, crop impact or severity.

## Nathan's interpolation boundary

Nathan owns interpolation at unobserved locations; ET owns QC at observed
stations. Agree a versioned contract before connecting implementations:
station ID mapping, location metadata, timestamp/hour-ending convention,
units, original observation, source quality, availability, provenance,
reference/policy versions and a labelled measured/derived distinction.

Agree whether QC flags exclude or downweight interpolation inputs. Preserve
raw observations for audit. Interpolated values must not silently replace
independent peers or the active frozen reference. Validate the adapter on a
small shared fixture; do not require merging both implementations before the
notification path can be exercised.

## Wind and later coverage

Wind/gust examples remain a separately validated deliverable. Before enabling
them, establish provider fields and historical coverage, units, speed versus
gust semantics, source QC, station exposure and peer suitability. Develop
variable-specific rules with calm-weather and missing-peer controls. A zero
alone does not establish a failed sensor. Reuse event/delivery contracts after
the wind detector passes its own checks.

## Demonstration and acceptance

| Check | Required result |
| --- | --- |
| Browser independence | New observations are checked with all dashboards closed; worker restart resumes progress |
| Temperature episode | Controlled eligible anomaly generates an opening with exact evidence; continuing readings do not spam |
| Severity/recovery | Configured escalation and established recovery notify once each; missing evidence does not fabricate recovery |
| Data health | Station absence, uniformly stale source, provider error and worker stoppage are distinguishable |
| Chill | Low/high entries and return to typical generate transitions; incomplete coverage withholds conclusions |
| Restart and duplicate input | Replayed ingestion, overlapping attempts and restart preserve one logical delivery per transition |
| Failed/uncertain send | Recoverable failure retries; uncertain acceptance is reconciled without a blind resend |
| Unsubscribe | Opt-out suppresses queued and future deliveries and survives restart |
| Historical/stale event | Old resolved anomaly is withheld or explicitly summarized; it is never presented as a fresh live reading |
| Mode isolation | Replay/injected packets remain labelled and cannot reach real recipients accidentally |
| Real delivery | Selected opted-in test phone/channel receives the actual message; provider/receipt evidence records the actual outcome |
| Nathan adapter | IDs, units, timestamps, quality and measured/derived status survive the agreed shared fixture |

For a live pilot, also demonstrate host-restart supervision, durable storage
recovery and operator visibility of a stopped worker or failing delivery queue.

## SMS readiness and honest demo modes

“Use trial SMS” is a dependency to verify, not a delivery guarantee. Current
[Twilio trial documentation](https://www.twilio.com/docs/usage/trials/try-out-sms)
restricts trial recipients and requires predefined message bodies. A new trial
therefore cannot be assumed to send custom Terra alerts. An upgraded sender
must meet its applicable registration/verification requirements.
[Twilio's toll-free guide](https://www.twilio.com/docs/messaging/compliance/toll-free/console-onboarding)
also requires approved verification before US/Canada toll-free SMS delivery.

Name the demonstrated mode: real SMS, another real opted-in channel or simulated
transport. A Twilio template-only send may prove transport/account access, but
does not prove delivery of Terra's custom alert. Simulated or alternate-channel
delivery does not fulfill the SMS acceptance check. Resolve account readiness
early so a carrier approval dependency does not surprise the demonstration.

## Proposed implementation sequence and open decisions

1. Finish ML removal, preserve active statistical/data/evidence identities,
   verify the clean source and publish a reviewable checkpoint.
2. Run the existing logic from a browser-independent worker with explicit
   freshness, service health and event identities.
3. Add durable recipient delivery, subscriptions and one working opted-in
   transport; exercise failure, recovery, opt-out and restart.
4. Validate both chill and data-health categories through the same path.
5. Integrate Nathan's agreed boundary and add separately validated wind coverage.

Open choices: hosting/operator owner; publication-aware polling and end-to-end
latency target; working sender/account and first real channel; warning/urgent
routing and quiet hours; whether extra chill severity bands are useful; Nathan's
exact schema; wind milestone. These remain visible decisions rather than
unverified production claims. The current task prepares this specification
and cleaned checkpoint; it does not deploy a service or send growers messages.

