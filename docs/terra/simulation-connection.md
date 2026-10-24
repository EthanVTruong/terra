# Simulator connection

The chat's **+ → Open simulation** connects the current browser session to an
isolated historical replay and opens `/stations`. The station controls and the
ordinary Terra conversation then share the same injected observations, frozen
QC and estimator. Station changes update native farm notes and retained evidence.

The connection copies the selected farm's crop/location and existing conversation
with its original immutable note evidence. Current readings, watch state and SMS
subscriptions stay in the live journal. A farm created during simulation is an
injected farm. The runtime's existing ChatGPT writer remains available for new
grower notes and questions; connection itself does not invoke it. Real SMS stays
disabled for simulation.

`GET /api/simulation/connection` reads connection state.
`POST /api/simulation/connect` accepts `{ "orchard_id": "optional-existing-id" }`.
Station lab’s **Return to live chat** control uses `DELETE /api/simulation/connection` to restore this browser’s live selection. Both
mutations require the existing local-session control token. The same-origin
HttpOnly session cookie is shared by the chat and station tabs. Other browsers,
the live monitor worker and its journal keep their normal source.

Replay journals remain below the chosen runtime state directory in
`simulator-sessions/`; disconnect does not delete them. Connections last for the
running server and browser session. Restarting the server requires reconnecting.
The station client must be built with `pnpm --dir web/demo build` alongside the
normal web build. Historical readings are identified as replay, and do not claim
current CIMIS coverage or handset delivery.

Each browser document captures the opaque `source_context` from connection status before rendering and sends it as `X-Terra-Source-Context` on API requests. A tab that switches source or a server restart invalidates earlier document contexts; stale requests fail with409 and require an explicit reload. Unknown simulator cookies fail closed and can recover only through guarded connection controls. Switching waits for active requests and durable pending conversation work, including onboarding. The standalone replay server remains isolated and identifies itself with `replay_only: true`.
