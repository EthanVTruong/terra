# ChatGPT plan connection

Terra is an MIT-licensed, local-first application. Its optional writer can use an
eligible user's ChatGPT plan through OpenAI's supported open-source OAuth flow.
Authentication and permission to use the plan are separate. A successful identity
sign-in without `chatgpt.tokens.use.direct` cannot make an inference request.

No existing Codex, browser or ChatGPT application credentials are read or reused.
A user explicitly chooses Continue with ChatGPT and completes OpenAI's sign-in
and permission screen themselves.

## Run locally

Run `python run_terra.py`. The default saved-example preview includes ChatGPT
sign-in with its own protected state in `.terra-local/preview/chatgpt`, while
live weather, the background worker and SMS remain disabled. Opening the app or
reading connection status does not begin OAuth, fetch a model catalog or generate
a response. Choose Continue with ChatGPT yourself to begin sign-in.

`python run_terra.py --no-chatgpt` intentionally disables the connection controls
for that session without erasing its saved connection. Live mode and the normal
`python -m terra_monitoring.runtime` command also enable ChatGPT controls unless
`--no-chatgpt` is supplied. The Windows service helper uses the same runtime.
The embedding factory remains opt-in (`chatgpt_enabled=True`).

Open Message writing from the account icon, choose Continue with ChatGPT, complete
OpenAI's consent, and choose a model from the returned account catalog. No API key
is needed for this route. New eligible notes use that provider; simply opening or
refreshing the feed makes no inference request and never rewrites old notes.
Without sign-in, permission, or a selected model, new notes retain the template.

State defaults to `<runtime-state>/chatgpt`; an explicit absolute
`--chatgpt-state-dir` supports a stable local vault across checkouts. Back up the
whole ignored state directory privately. Do not commit it or copy Windows DPAPI
credentials to another user. A ChatGPT-enabled ASGI application instance has one
lifespan; create a new app instance when restarting an embedding host. Ordinary
CLI process restarts create a fresh instance and retain the protected connection.

## Connection and privacy

- Each installation retains one opaque host ID. Registrations keep their issued
  client ID, verified subject and credentials separate, including registrations
  with the same email address.
- The authorization-code flow uses PKCE, random state and nonce, an ephemeral
  `127.0.0.1` callback listener and verified RS256 ID-token signatures, issuer,
  audience, expiry and nonce. Callback request logging is disabled.
- Credentials stay in the app's ignored local state directory. Windows uses
  current-user DPAPI encryption; Unix uses owner-only directories/files. Writes
  are atomic. Native file locks serialize rotating refreshes across processes.
- Browser-facing endpoints expose only connection/account labels, consent state,
  selected model and usage state. Tokens and callback secrets are not returned.
  All controls use the existing local Host/Origin/session-token guard.
- Sign-out revokes the renewable session when reachable, then clears local tokens
  while retaining registration/host mapping. If revocation cannot be confirmed,
  disconnect the app in ChatGPT settings.

## Model requests and usage

The model picker reads the connected account's live catalog. It shows returned
display names and sends only the selected slug. No model is guessed, selected by
default, or claimed to be cheapest without account-specific evidence.

The plan route uses the public Responses endpoint with `store:false`,
`stream:true` and an input array. It omits unsupported options, including
`max_output_tokens`. A completed SSE event is required; partial text, failed or
incomplete responses never become a finished message. Requests, streams, outputs,
timeouts and model inventories are bounded. An HTTP inference request is not
automatically retried.

The observed plan gateway omitted Content-Type and returned an empty terminal
`output` after completed output items. The adapter accepts this bounded case only
after a completed assistant item, text-done and deltas agree exactly, followed by
`response.completed`. Refusals are rejected on every completion path. An empty
envelope or partial text alone is insufficient. The catalog allows up to 1 MiB of
metadata but retains only visible model slugs and display names.

Usage-limit failures pause new plan requests until explicit user action.
Transient unavailability has bounded persisted backoff. Concurrent state updates
preserve another request's pause and model choice. There is no silent switch to
API-key billing. [Manage usage](https://chatgpt.com/settings/usage) is available
in the connection control. Plan usage shares the user's existing allowance; it
does not provide extra tokens or promise a separate per-dollar price.

Detection, event eligibility, measurements, clocks and notification decisions stay
in Python. The normal ChatGPT-enabled runtime now uses the selected model as its
[contextual conversation and insight engine](conversational-engine.md). It receives
the actual current message, bounded recent same-farm dialogue, saved crop/setup
context and Python-computed facts. It understands follow-ups, asks one focused
clarification when necessary, and writes concise useful explanations of selected
alerts. This supersedes the earlier canonical-intent/wording-only interface.

Coordinates, credential-like strings and raw station arrays are excluded from
assembled context. Original old-note facts remain separate from current readings.
Quantities use cited application-owned bindings; structured validation and
state-specific checks reject malformed responses and unsupported assertions,
without a fixed prose vocabulary. Fallback is entirely local, with no second
provider attempt or API-key switch. Original inputs, source clocks and event facts
remain immutable. The legacy explicit API-key writer retains its own contract.

## Current verification boundary

Offline tests cover OAuth/identity, protected storage, cross-process refresh,
catalog and SSE handling, concurrent usage state, public API guards/redaction,
UI state helpers and lifecycle. These tests use fake credentials and controlled
transports. They do not prove real account eligibility, consent or inference.
Those require the user's own connection and an actual completed model response.

On October 24, 2026, the operator completed genuine consent with plan permission;
the account catalog exposed seven visible models. GPT-6 Luna was explicitly
selected for this installation. A saved-weather replay ran the frozen QC, Nathan
estimator, persisted monitor events and production writer: two notes passed
generation validation and were published as `chatgpt_plan`; the welcome note
failed validation and used `template`. The three replay requests reported 2,026
total tokens. Earlier adapter diagnostics also made inference requests; this is
not a claim about total account usage. No SMS was requested by this replay.

After ET approved a more natural texting voice, a separate weather-only replay
with no chosen crop, variety or agronomic target produced two validated plan-written
notes from the same real QC/estimator/watch path. Those two requests reported 758
tokens. It suppresses the setup welcome for this isolated review, keeps explicit
replay labels, and activates neither a live worker nor SMS. Previously saved
preview records are preserved in their separate state directory.

The preceding real-account receipts describe earlier wording paths, not proof of
the new contextual engine. The [Terra conversation contract](conversation-contract.md)
and [engine implementation](conversational-engine.md) distinguish current behavior
from those historical checks. Final integrated-source inference evidence belongs
to the operator's runtime verification; controlled tests alone do not establish it.

The isolated preview runs without a monitor worker or SMS transport activation.
It shows the labelled weather-only replay and leaves existing main messages intact.

Official references:

- [Overview](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
