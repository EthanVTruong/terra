# Twilio phone alerts

Terra can send selected, persisted orchard notifications to one privately configured phone.
The **Phone alerts** panel also runs a controlled spike through the frozen
statistical detector. In Terra-content mode its SMS includes the evidence and
is labelled **TERRA DEMO**; trial-template mode sends a generic Twilio sample
while the evidence stays in Terra. Both are explicit tests using simulated
weather with real SMS transport when configured. Saved assessments and uploaded
files never send messages.

Ordinary location checks remain read-only. The SMS module adds no scheduler:
the farm monitoring backend owns observation checks, event selection, the durable
outbox and browser-independent execution. It calls the shared transport with an
immutable selected-message envelope. Local inbox publication and SMS delivery
remain separate facts. The task's combined integration owner verifies that worker
boundary; see [always-on monitoring](always-on-monitoring.md).

## Account readiness

1. Sign in at [Twilio Console](https://console.twilio.com/). Complete email/phone
   verification yourself. Select the account you want to use for Terra.
2. Use an SMS-capable Twilio sender that is approved for your destination.
   US toll-free senders require approved toll-free verification; US local
   application messaging requires its applicable A2P 10DLC registration.
3. Check account restrictions before sending custom Terra text. Current
   [trial documentation](https://www.twilio.com/docs/usage/trials) limits trial
   messaging to verified recipients and predefined content. A template or virtual
   phone test proves only that limited transport path, not receipt of a Terra alert
   on your mobile. Upgrade/registration may be necessary.
4. Copy the Account SID and an existing Auth Token or API key into the private
   server configuration. Never paste credentials into the frontend, a Git commit,
   screenshots, or chat. Creation of a new key, purchases and regulated sender
   submissions need the account owner's review.

No inbound webhook or public tunnel is needed for this local demo. Delivery
status is read from the Twilio Message resource by SID. Carrier/provider reporting
can stop at `sent`; `queued` or `sent` does not establish handset receipt.

## Trial-template placeholder

For a trial-only demo, set `TERRA_SMS_CONTENT_MODE=trial_template` in the private
server configuration. Terra then submits Twilio's fixed `sms_internal_alerts`
sample when an existing eligible notification or explicit simulated-flag test
reaches the dispatcher. The phone receives a generic Twilio sample. The actual
finding and evidence remain in Terra. The interface labels its receipt
**Trial placeholder**, so delivery of that sample never implies delivery of the
grower message shown in the inbox.

This mode requires a verified trial recipient and valid private credentials; it
does not require an owned `TWILIO_FROM_NUMBER`. The request contains only `To`
and the template identifier in `Body`, as documented by the
[trial Messaging API](https://www.twilio.com/docs/usage/trials/try-out-sms#send-an-sms).
It retains the same orchard opt-in, alert selection, duplicate suppression,
attempt cap and receipt journal. It does not text every raw station outlier.
No webhook, public tunnel, new worker or paid upgrade is required for this mode.

Terra still rejects expired events immediately before submission. The trial
request omits unsupported `ValidityPeriod`, so it cannot bound Twilio's queue
time after acceptance. Delivery receipts persist the content mode and template
used for that attempt, including across restarts and later configuration changes.
The default `TERRA_SMS_CONTENT_MODE=terra` keeps custom Terra text and its approved
sender requirement. An invalid mode fails closed.

## Private configuration

Install the existing minimal requirements and build the frontend:

```powershell
python -m pip install -r requirements-terra-dev.txt
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

Create `.terra-local/twilio.env` locally (Git ignores this directory), or set the
same values in the server environment. A file is loaded only when explicitly
selected with `TERRA_SMS_ENV_FILE`; process variables override file values.
Use real credentials only in that private file:

```dotenv
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
TERRA_SMS_TO_NUMBER=+1...
TERRA_SMS_ENABLED=true
TERRA_SMS_DEMO_ENABLED=true
TERRA_SMS_DB_PATH=.terra-local/sms.sqlite3
TERRA_SMS_MAX_PER_DAY=10
```

Alternatively replace `TWILIO_AUTH_TOKEN` with `TWILIO_API_KEY_SID=SK...` and
`TWILIO_API_KEY_SECRET=...`. Never set `VITE_*` variables for credentials.
The sender and recipient use E.164 format: `+` followed by country code and number.
The default daily cap is ten attempts, including failed/uncertain attempts.

Start the local server from this checkout:

```powershell
$env:PYTHONPATH = "backend"
$env:TERRA_SMS_ENV_FILE = ".terra-local/twilio.env"
# Optional: required for current weather; the synthetic SMS demo needs no CIMIS key.
$env:CIMIS_APP_KEY = "<your CIMIS AppKey>"
python -m terra_monitoring.server --host 127.0.0.1 --port 8513
```

Open <http://127.0.0.1:8513> in the browser. Use the built frontend on the same
port for SMS controls; a Vite proxy on another origin is not an authorized SMS
origin. Ordinary location queries still work through the development proxy.

## Run and verify

1. Open **Phone alerts**, then **Refresh setup**. Check the masked recipient and
   that delivery is enabled. Configuration status reports presence/format only;
   it does not prove that Twilio has approved the sender or accepted credentials.
2. Click **Send simulated flag to my phone**, or **Send trial placeholder to my
   phone** in trial-template mode. The server generates eight hours
   of synthetic station readings with a final 120°F Davis spike, evaluates the
   real frozen detector, and sends its finding. It accepts no browser-supplied
   phone, body, observation or arbitrary assessment.
3. Check your phone for the labelled Terra demo or the configured Twilio trial
   sample. A trial placeholder receipt does not mean the actual finding was sent.
   Use **Check delivery** to
   read Twilio's latest receipt without resending. Retain the Message SID for
   correlation with **Monitor → Logs → Messaging** in Twilio Console.
4. Repeated clicks/reloads for the same demo retain the event identity. After a
   terminal result, **Prepare another demo** allows a deliberate new message.
   If the outcome is uncertain, inspect the provider log before any new send.
5. **Query location** stays read-only even with SMS enabled. Production texts
   consume the farm monitor's selected, persisted notifications through
   `SMSDispatcher.dispatch_event(envelope)` (`dispatch_message` is an alias).
   Suspect station details cannot bypass the farm's orchard-message policy.
   On startup and subsequent ticks the worker drains unexpired messages and
   rechecks their relevance. Reusing an immutable event never creates a second
   accepted logical send. Replay/injected envelopes are withheld from this path.
6. **Pause all phone alerts** persists suppression across restarts. Re-enabling
   requires `python -m terra_monitoring.sms enable` with explicitly enabled valid
   configuration. Twilio also enforces its own
   destination opt-out restrictions. This local demo is not a grower subscription
   or inbound STOP-management system.

## Reliability and privacy

The SQLite store records each logical attempt before the HTTP send, with a stable
identity and provider SID/status. A process crash or transport timeout after
possible acceptance is treated conservatively; no blind automatic resend occurs.
Known provider failures remain visible. This prioritizes avoiding duplicate texts
over pretending that every network interruption is recoverable automatically.

Farm messages rejected definitively by Twilio with HTTP 429 and no acceptance/SID
can retry only when the caller rechecks and dispatches the same unexpired,
eligible immutable envelope. The persisted backoff is 30 then 120 seconds, with
three attempts total. Ambiguous responses, timeouts, crashes after claim and
unknown acceptance never retry automatically. The per-attempt journal retains
each rejection and enforces the daily cap across process restarts.

The worker calls `reconcile_pending(limit=10)` on startup/ticks independently of
the browser. It polls only saved nonterminal SIDs and never sends. Results retain
the original `source_event_id` after restart, so the farm store can update its
transport receipt separately from local inbox publication. The canonical fields
are `status` (`provider_accepted`, `delivered`, `retry_wait`, `uncertain`, etc.),
`provider_status`, `provider_message_id`, `updated_at_utc`, `source_event_id`, and
the transport `event_id` used by `receipt()`. Failed polls rotate through bounded
batches so one unavailable receipt does not starve others.

The browser sees masked phone numbers and redacted status codes. Account secrets,
full recipient numbers and provider error bodies never enter API responses.
SMS browser operations require a server-generated control token, a literal loopback Host
and an exact same-origin browser request. This is a local single-user control,
not Internet-facing authentication; keep the server bound to loopback.

`assessment.py` and `/api/assess` remain pure. There is no `/api/sms/check` endpoint.
`/api/sms/demo` is a separate explicit real-send boundary. It returns honest
transport status; polling a receipt never triggers a send. The farm backend's
internal `terra-notification-v1` envelope carries its event identity, immutable
text, mode, category, fresh eligibility/check time and expiry. Numerical temperature
evidence must be fresh; a monitoring-limited notice can describe stale/missing
data, and a daily chill update can describe the latest completed day. The farm
backend owns those category-specific calculations and relevance decisions.

## Checks

```powershell
python -m pytest --noconftest -o pythonpath=backend tests/test_terra_sms.py tests/test_terra_sms_server.py -q
python terra.py check --base <actual-reviewed-upstream> --task terra-twilio-sms
pnpm --dir web build
pnpm --dir web test:sites
```

Automated tests use an injected Twilio transport; they do not deliver SMS or use
account credentials. They verify the real detector-to-dispatch composition,
durability, concurrency, duplicate suppression, noncurrent isolation, safe errors,
receipt transitions and local HTTP authorization. Real delivery additionally needs
an accepted Twilio message and the account owner's phone confirmation.
