# Backend cleanup

The October 24 audit fixes preserve the QC equations, frozen reference, model
parameters and training/repair pipeline. Native changes only reject malformed
provider inputs and exclude stations outside their operating dates.

| Files | Change |
| --- | --- |
| `chillwatch/estimate.py` | Apply station operating dates before lapse fitting and provenance. |
| `chillwatch/normalize.py`, `sources/cimis.py` | Accept only legal CIMIS hours and recognized Fahrenheit/Celsius units; retain midnight `2400`. |
| `farm_ops/service.py`, `store.py` | Recover from three persisted consecutive valid hours, reserve question IDs before generation, and retrieve old Details by indexed identity. |
| `farm_ops/writer.py` | Generate concise event prose with field-bound quantity placeholders and conservative status/role checks. Python renders names, numbers, source times and demo labels. Keep deterministic fallback and the injectable `write(packet)` interface. |
| `live.py` | Bound retained response/attempt state and back off after failed refreshes. Cached responses retain their original retrieval time. |
| `configuration.py`, `runtime.py`, `sms.py` | Share explicit environment parsing and SMS journal resolution across runtime and operator commands. Initialize transport migrations once per store instance. |
| `server.py`, `farm_ops/api.py`, `farm_ops/__main__.py` | Share HTTP assembly, validation and method errors; retain explicit worker lifecycle. |

The unreachable raw-station SMS adapter is removed. Farm-selected immutable
notification envelopes remain the delivery boundary. Existing legacy tables
are retained; no delivery history or source data is deleted.

## State and operator implications

Keep the existing runtime state directory across restarts. Question IDs now
reserve generation durably: a concurrent or interrupted unfinished request
returns an explicit in-progress error. It is not regenerated automatically,
because the provider may already have completed it. A new deliberate question
may use a new request ID.

The default shared SMS journal is `.terra-local/runtime/sms.sqlite3`. Explicit
`TERRA_SMS_DB_PATH` configuration wins, including a selected private environment
file. Use the same `--state-dir` for the runtime and SMS/farm operator commands
when using a custom state directory. An older explicit journal remains usable
at its existing path; no file is moved automatically. Startup rejects a path
change that would abandon an existing delivery/attempt journal or saved pause. Reconcile
the retained records and deliberately select one authoritative journal before
restarting in that case.

The supported combined runtime owns continuous monitoring. Compatibility serve
commands use the same HTTP assembly with no implicit background worker. Source
failures preserve cached evidence and use a bounded retry delay; an explicit
refresh can bypass that delay.

Optional generated wording uses a bounded vocabulary and explicit fact roles.
Unfamiliar or ambiguous prose falls back to the existing template. These checks
are conservative screens, not a proof of arbitrary natural-language meaning.
An explicitly injected generator shares the same validation and never silently
falls back to API-key billing.

## Verification boundary

Focused regressions cover malformed inputs, station dates, recovery catch-up,
restarts, concurrent question IDs, old message Details, source backoff/cache
limits, journal selection, HTTP validation, and invalid model output. Full
backend checks include synthetic native pipeline fixtures and PostgreSQL in CI;
the web build and packaging/delivery tests run separately.

No live weather ingestion, model fitting on real data, LLM request or SMS send
is needed for these checks. Actual phone delivery still requires the account,
sender and intended-recipient setup described in [Twilio setup](twilio-sms.md).
