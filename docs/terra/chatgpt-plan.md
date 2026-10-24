# ChatGPT plan connection

Terra is an MIT-licensed, local-first application. Its optional writer can use an
eligible user's ChatGPT plan through OpenAI's supported open-source OAuth flow.
Authentication and permission to use the plan are separate. A successful identity
sign-in without `chatgpt.tokens.use.direct` cannot make an inference request.

The current integration is being verified in an isolated preview before replacing
the shared runtime. No existing Codex, browser or ChatGPT application credentials
are read or reused. A user explicitly chooses Continue with ChatGPT and completes
OpenAI's sign-in and permission screen themselves.

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

Usage-limit failures pause new plan requests until explicit user action.
Transient unavailability has bounded persisted backoff. Concurrent state updates
preserve another request's pause and model choice. There is no silent switch to
API-key billing. [Manage usage](https://chatgpt.com/settings/usage) is available
in the connection control. Plan usage shares the user's existing allowance; it
does not provide extra tokens or promise a separate per-dollar price.

Detection, event eligibility, measurements, clocks and notification decisions stay
in Python. The model writes concise clauses from permitted facts; Python renders
the quantities, orchard name, source time and demo marker. Writer failures retain
a deterministic fallback. Existing published field notes remain immutable.

## Current verification boundary

Offline tests cover OAuth/identity, protected storage, cross-process refresh,
catalog and SSE handling, concurrent usage state, public API guards/redaction,
UI state helpers and lifecycle. These tests use fake credentials and controlled
transports. They do not prove real account eligibility, consent or inference.
Those require the user's own connection and an actual completed model response.

The isolated preview runs without a monitor worker or SMS transport activation.
It shows a copied field-note snapshot and leaves the running main app untouched.

Official references:

- [Overview](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
