# ChatGPT plan connection

Terra is an MIT-licensed, local-first application. Its optional writer can use an
eligible user's ChatGPT plan through OpenAI's supported open-source OAuth flow.
Authentication and permission to use the plan are separate. A successful identity
sign-in without `chatgpt.tokens.use.direct` cannot make an inference request.

No existing Codex, browser or ChatGPT application credentials are read or reused.
A user explicitly chooses Continue with ChatGPT and completes OpenAI's sign-in
and permission screen themselves.

## Run locally

Install `requirements-terra-dev.txt`, build the existing web client, and start the
normal `python -m terra_monitoring.runtime` command with `PYTHONPATH=backend` and
the existing private source configuration. The Windows service helper uses the
same runtime. ChatGPT controls are enabled by the CLI; `--no-chatgpt` opts out.
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
in Python. The normal runtime selects `voice="text"` for coherent proactive
weather-coverage notes. The model writes a brief text from categorical weather and
estimate states; names, crops, clocks, counts and technical causes are excluded
from that model input. State-specific checks reject reversed availability,
invented advice or quantities, and unrecognized wording. Fallbacks use the same
approved casual voice, including a stronger message when data is fully missing.

Exact event facts, source clocks and recovery counts remain in `llm_input` and
Details. The interface labels replay mode outside the bubbles and retains message
timestamps. Mixed-state updates, questions, noncoverage events and the explicit
API-key writer retain the earlier time-aware factual renderer until their own
voice contracts are expanded. Existing published field notes remain immutable.

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

The [Terra conversation contract](conversation-contract.md) is preserved separately.
This slice ships incoming notes and plan connection. Conversational onboarding,
reactions and silence outcomes remain separate acceptance work. The coverage-note
slice now follows the approved short texting style; other note types still need
their own voice reconciliation. Future changes must keep generated and fallback
notes consistent and preserve exact evidence.

The isolated preview runs without a monitor worker or SMS transport activation.
It shows the labelled weather-only replay and leaves existing main messages intact.

Official references:

- [Overview](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
