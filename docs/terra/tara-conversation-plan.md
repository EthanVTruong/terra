# Terra onboarding and conversation implementation

Status: active implementation goal, authorized by ET on October 24, 2026.
The [accepted contract](conversation-contract.md) remains the voice and behavior
reference. Completion requires implemented, verified behavior in the app.

## Product behavior

- Start with a short crop question, accept a natural grower answer and preserve
  crop/variety information without inventing a variety or crop requirement.
- Confirm a farm location using explicit device permission or manual coordinates/
  known reference choices. Persist setup and resume unfinished steps. Denied or
  inaccurate location must leave a usable manual path.
- Ask separately about supported notification delivery. Represent saved actions
  as compact events. Describe actual delivery capability, not merely permission.
- Ready means the profile is saved and the actual data/worker state supports
  monitoring. Show pending/unavailable states truthfully and preserve old notes.
- Provide a real composer and contextual farm/alert/chill replies. Keep messages
  short, make relevant comparisons concrete, and expose exact evidence separately.
- Benign acknowledgment may receive one attached thumbs-up. Simple thanks,
  repeated feed opening and ended conversations need no assistant message.
- Handle clearly unrelated requests, such as video-game chat, locally with a
  lightweight reaction/no-reply outcome. Never call a model just to classify them.
- Mixed messages containing a real farm question or request must handle that
  substance. An unrelated clause must not turn the app into a general chatbot.
- Scope is farm setup, supported monitored conditions, chill calculations, source
  reliability and explanations of Terra's own notes. Unsupported predictions or
  farm operations receive a concise grounded limitation, not invented advice.

## Implementation sequence

1. Build an authoritative local response router and persistent request ledger.
   Classify message/reaction/none before provider use. Preserve farm/alert context,
   pending requests and idempotency across refresh/restart.
2. Add durable onboarding/profile fields and guarded APIs. Keep immutable evidence,
   monitoring lifecycle, actual consent and scientific boundaries intact.
3. Add the conversation/onboarding UI within the accepted app shell, including
   real user messages, attached reactions, honest local receipts and no noisy
   reopening. Keep location/evidence/demo controls usable.
4. Reuse Terra's own ChatGPT provider/selected model and explicit plan permission.
   Route only relevant bounded fact requests to it. No shared Codex credentials,
   silent API-key fallback, off-topic usage, or automatic paid retries.
5. Reconcile current ChatGPT/demo/UI work in the root integration copy. Preserve
   independent source branches and make one reviewed repair cycle for overlaps.
6. Verify, merge and run the accepted flow with retained runtime journals. Record
   mock versus real connection/inference evidence separately.

## Completion evidence

Backend verification must cover supported questions, contextual follow-ups,
off-topic/instruction-bypass/mixed-intent input, gratitude/acknowledgment, explicit
preference requests, unavailable facts, request retries/concurrency/restarts and
call counts. No-op/off-topic paths must prove zero provider calls. Successful
connected eligible generation must use the configured ChatGPT plan connection.

Browser verification must cover first use, saved/unfinished setup, manual and
device-location success/denial, notification choice/denial, real composer replies,
attached reaction, no reply, quiet reopening, loading/failure/usage pause, evidence,
mobile layout, keyboard control and reduced motion. Permission UI is simulated
in automated tests; actual account consent stays with the user.

Run the scoped repository check, meaningful focused backend/web tests, production
build and final-head CI. Verify the actual merged runtime, source revision and
retained data. Do not mark the goal complete while any explicit requirement is
missing or actual ChatGPT readiness/inference remains unverified.
