# Tara in the grower workflow

Tara opens in the existing monitoring view. The implementation follows the
[conversation contract](conversation-contract.md) and
[implementation plan](tara-conversation-plan.md).

## Setup and return

1. Ask what the grower is growing. Save multiple crops and supplied varieties;
   do not assume an almond variety or a chill requirement.
2. Show one **Use my location** button. Its explicit browser permission flow
   supplies a fresh device position; the server resolves ground elevation and
   saves the shared growing location automatically. No coordinate, elevation,
   farm-name, station-picker or confirmation form appears in onboarding. A denied,
   unavailable or failed lookup leaves a concise retry path.
3. Offer browser notifications separately. They work while Terra is open;
   declining keeps in-chat updates. This preference does not enable SMS.

Saved location and notification choices appear as compact events. Readiness uses
the actual monitor/data state. A saved farm with no usable weather stays pending.
Reopening resumes setup or the saved conversation without generating a greeting,
another model request, or a repeat browser notification.

This button-only flow supersedes the original manual-location onboarding after
ET's October 24 correction. Location is shared once for monitoring that point,
so growers should use it where they grow. The internal label is **Shared location**,
not an inferred farm name. Ground elevation comes from the official
[USGS point-elevation service](https://epqs.nationalmap.gov/v1/docs); browser
altitude and guessed station elevations are not substituted. Provider failures
save no location profile. Existing regional peer selection, QC and coverage
requirements remain authoritative and can leave monitoring unavailable.
The legacy explicit-location API remains compatible for existing callers.

## Local decisions before model use

The application chooses message, attached reaction, or silence before inference.
Simple thanks gets no reply. Acknowledgments and clearly unrelated requests get
an attached thumbs-up. New or ambiguous subjects receive a local clarification.
Mixed input retains a supported farm question and discards unrelated clauses.
The legacy orchard-question route applies the same scope boundary.

Saved crop/location/preferences, monitor status, the supported chill-method FAQ,
and comparable historical gap arithmetic are answered locally. Unavailable totals
also stay local. A gap requires actual matching method, cutoff, adequate coverage
and enough reference seasons; fixture values such as 180/220 are never defaults.
Old comparisons retain their year and completed-season meaning.

Eligible fact explanations reuse Terra's selected ChatGPT plan connection and
explicit consent. The model receives bounded facts and canonical intent, without
the raw conversation. Its prose must pass the existing quantity/role validator;
failure uses a grounded fallback without an automatic model retry. Interactive
questions never silently switch to API-key billing. Seasonal totals retain useful
source dates; exact timestamps and immutable inputs remain under Details.

## Durable requests and recovery

Each operation has a stable request ID and exact payload. Reload or a lost HTTP
response can recover that saved operation without creating another question.
An active or unknown request owner remains locked. A process-exit proof uses
PID plus process-start identity, never elapsed time alone.

Only a demonstrably exited owner enables an explicit continuation choice. The
grower acknowledges that the earlier request may have used allowance; a new
acknowledgment ID saves an uncertainty notice and permits a new question. It
never retries the old inference. A completion race instead retrieves the actual
saved reply under its original identity. Owner identifiers stay private.

## Interfaces and evidence

The local guarded API exposes GET `/api/tara/conversation`, POST `/api/tara/messages`,
POST `/api/tara/setup` and POST `/api/tara/reconcile`. The existing SQLite farm store
also persists conversation turns and requests. No new scientific model, station
calibration, ingestion run, SMS activation, or closed-app push service is added.

Verification includes local routing and call counts, actual runtime/provider
assembly, strict factual fallback, saved setup, retries/concurrency, real process
exit, browser permissions and browser delivery semantics. Windows Edge testing
verified actual onboarding, attached reactions, silence, quiet reopening, a
validated real ChatGPT-plan weather-support reply, immutable Details lookup and
320px layout. Native permission responses are simulated in browser tests. The
account used Terra's already approved connection; no Codex credential is reused.

The first live unavailable-total attempt completed at the provider but failed
the factual wording validator. The final path answers that absence locally with
zero generator calls; the separate eligible weather reply passed validation.
Runtime deployment evidence and final CI belong to the integration handoff.
