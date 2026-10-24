# Phone connection interaction proposal

Design review, 2026-10-24. The user selected the existing verified phone for the
demo. The UI now checks delivery readiness; no new verification service is added. The existing approved Terra layout and compact copy remain
the visual target. The separate design preview makes no network calls or SMS requests.

## Decision

For the selected demo route, **Connect phone** checks the existing local SMS
configuration. It does not send a code or enable the server. The response shows
Ready, Setup required or Unavailable. Requests have a 10-second deadline and
retain keyboard focus. After readiness, a separate **Field note alerts** switch
controls the existing orchard subscription. A paused subscription preserves the
phone connection. Per-message delivery receipts remain separate from both.

The former checkbox called the orchard `sms-opt-in` endpoint. It never verified
or connected a phone. The revised switch performs that same preference change
only after connection readiness makes it useful. Current `/api/sms/status` reports configuration, enable flags,
suppression and a masked fixed recipient; it supplies no verification challenge
or proof of phone ownership. `configured` alone must not become `Connected`.

## Selected demo route

The configured recipient is fixed and already verified outside Terra. No number
entry or code screen is needed. Connection checks read `/api/sms/status` and the
existing orchard preference in parallel. Missing credentials, delivery disabled,
a paused sender, missing recipient or a network error cannot claim Connected.
Ready means the local SMS configuration is enabled for its masked fixed recipient.
Each message still has its own delivery outcome. Automatic texts remain subject to
the existing orchard opt-in and delivery gates.
The UI does not turn on sending, change the recipient or call a provider during
a connection check. Trial-template mode keeps its notification-only wording.

## Full enrollment reference (future scope)

| State | Visible content and action | Transition requirement |
| --- | --- | --- |
| Not connected | Short purpose line; Connect phone | Open number entry; no subscription checkbox |
| Number entry | Phone field; Send code; Cancel | Server validates and starts a challenge after the user requests it |
| Pending verification | Check your phone; one code field; Verify phone; Change number; timed Resend | Server accepts the code; entering a phone or clicking Send code is insufficient |
| Phone verified, setup incomplete | Verified status; delivery setup explanation; Check connection | Delivery service must also be ready |
| Connected | Masked number; Change number; Field note alerts switch; explicit test notification | Verified binding and usable delivery configuration |
| Alerts paused | Phone remains connected; switch is off | Confirmed orchard subscription change, not removal of the phone |

Show actionable waiting states rather than an indefinite Connecting spinner. A
short Sending code or Verifying label is appropriate only while its request runs.
Use one code input with numeric keyboard, paste support and one-time-code autofill.
Show the full number during initial confirmation so typos can be found; mask it
afterward. Resend cooldown is server-enforced; a 30-second initial buffer is the
proposed default, informed by Twilio guidance.

Wrong/expired codes remain beside the code field. Allow correction or a new code.
A lost response must not automatically resend. Page reload checks the existing
challenge. Keep the old verified number until a replacement is successfully
verified. Confirm subscription changes from the server before claiming success.

## Logistics and implementation boundary

For full enrollment, add a local protected connection API backed by a managed
verification service such as Twilio Verify. It starts a challenge, checks the
code, stores the verified recipient binding and exposes durable connection
status. Keep provider credentials and code validation on the server. Persist the
phone binding locally and independently of model inference. The existing
orchard opt-in endpoint remains the explicit alerts preference after connection.
This changes the fixed-recipient boundary and requires the transport owner’s
review; no such backend change is implemented by this proposal.

After a qualifying event, Terra saves the actual field note, queues an eligible
SMS notification and reconciles provider receipts. Queued or sent remains
unconfirmed delivery. A connected phone does not imply every message arrived.

For the current demo, prefer the already-verified fixed phone and an honest
delivery-readiness state. Current Twilio trial documentation limits outgoing
bodies to predefined templates. The ongoing trial-placeholder work therefore
keeps the actual farmer wording in Terra and labels the SMS as a notification
only. Do not present a trial template as the actual field note or add an
unimplemented verification wizard to the live controls.

User selected the fixed verified phone for the demo. Full number-and-code
enrollment remains future scope. The live UI now implements only the selected
readiness flow; backend configuration and provider operations remain unchanged.

## Sources and evidence

- [Apple HIG: Toggles](https://developer.apple.com/design/human-interface-guidelines/toggles): opposing on/off settings; the connection task needs an action and states.
- [GOV.UK: Confirm a phone number](https://design-system.service.gov.uk/patterns/confirm-a-phone-number/): one code field, autofill/paste and correction paths.
- [Twilio: Verification best practices](https://www.twilio.com/docs/verify/developer-best-practices): initial full number, later masking, verified binding and retry buffers.
- [Twilio: Try out SMS](https://www.twilio.com/docs/usage/trials/try-out-sms): current trial template-body restriction.
- Repository evidence: `web/src/SmsControls.jsx`, `backend/terra_monitoring/sms.py`, `backend/terra_monitoring/server.py`, `backend/terra_monitoring/farm_ops/api.py`.
- Selected preview: thread-scoped `terra-verified-phone.html`, with a fictional masked number. The earlier `terra-phone-connection.html` retains the unselected enrollment study. Actual React UI checks cover the real setup-required response plus an isolated ready/connecting fixture, orchard preference on/off, retained focus and mobile layout. Test fixtures block all provider sends. No real message was sent.


## Final implementation verification

The live UI implements the selected fixed-phone route with explicit Not connected,
Connecting, Setup required, Ready, Sending paused and Unavailable states. Missing
recipient/error flags cannot claim Ready. Network requests have a 10-second
abort deadline and stale-status response guards. Connection checks read status
and orchard preference in parallel without changing either. Readiness does not
automatically opt in the orchard.

The subscription switch confirms its value from the existing server response.
Keyboard focus returns after a busy control is re-enabled, without taking focus
back after the user moves away or dismisses the panel. Touch controls use 44px
minimum targets. Check delivery appears only for an existing test event, including
a retained event after reload or while sending is paused. Existing event IDs,
uncertain-send guards and provider receipt semantics remain intact.

Production build, 14 UI tests, four packaging tests and scoped coordination /
filesystem validation pass. The actual running server reports Setup required.
A separate HTTP fixture verified the same React components in Ready and Connecting
states, a stored preference change and mobile layout without any provider calls.
The fixture is isolated from the real Terra API; its POST routes reject sends.
Screenshots are under `.terra-local/phone-connection/`.

Integration note: origin/main advanced during this pass with backend cleanup.
Scope/record validation passes against the declared task base 78e835d; the UI
branch preserves that base. The integration owner must reconcile current main
and run its configured integration checks before merging this branch.
