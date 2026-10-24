# Terra's contextual ChatGPT engine

The selected ChatGPT plan is the primary conversational engine in the ordinary
ChatGPT-enabled runtime. It receives the current grower message, recent same-farm
dialogue, and Python-computed factual statements. It understands follow-ups,
decides whether to answer, ask a focused clarification, react, or stay quiet, and
writes concise explanations of monitor-selected changes.

Python remains authoritative for measurements, QC, estimation, chill arithmetic,
reference eligibility, source clocks, event selection, recovery, settings,
permissions, and delivery. This change introduces no scientific model, crop-risk
threshold, additional provider, automatic sign-in, or silent billing fallback.

## Context and interpretation

`conversation/context.py` assembles at most 24 factual statements and 16 recent
turns, under 12,000 history characters and a 40,000-byte packet. Facts cover saved
crops, setup, actual monitoring state, current saved weather, chill totals,
eligible comparisons, and immutable referenced notes. Unknown states stay unknown.
Private coordinates, credential-like strings and raw station arrays are excluded.
Recent user messages and model replies are dialogue, not authoritative facts.

An explicit old-note reference keeps its original assessment. Implicit follow-ups
retain the prior answer's cited facts separately from current conditions, so a
later reading cannot silently replace the evidence under discussion. Questions
such as “Does this represent an anomaly?”, “What does that imply?” and “What else
do you need?” reach the model without a fixed phrase or vocabulary admission gate.

Necessary clarification is a model decision, not a fixed fallback question.
Complete answers do not acquire automatic follow-up questions. Thanks can end
quietly, while an acknowledgment may receive an attached reaction. The existing
API still exposes message/reaction/none; a clarification is a message whose
`response_kind` is `clarify`.

Crop entry starts empty and accepts explicit custom crop declarations. For an
unfamiliar bare crop answer, a local candidate is not saved until the model
interprets it as the requested crop choice and advances the focus to location;
the existing profile validator then validates it. A greeting is not a crop.
No almond, cultivar, growth stage or agronomic target is silently assumed.

## Grounding and concise alerts

`conversation/engine.py` calls the existing selected plan client once with bounded
context and a structured response schema. The model cites factual IDs and uses
application-owned placeholders for quantities. Python inserts exact values after
checking citation and placeholder ownership. Output checks reject malformed or
incomplete responses, invented numbers, unknown references, explicit unsupported
actions, false delivery claims and several state/availability contradictions.
Qualitative interpretation uses model instruction-following plus these checks;
it is not a formal semantic proof of every sentence.

`farm_ops/insight_writer.py` uses the same engine for proactive notes. Python
already selected the meaningful events; the model explains what changed and why
it matters for the estimate or comparison. Every selected event must be cited.
Coverage loss and recovery, provisional station discrepancies and missing-data
states remain distinct. Crop damage, causes, forecasts and treatment advice
cannot be inferred from these facts. Material saved/replay chronology is appended
locally, and original measurements and event inputs remain immutable in Details.

## Actions, persistence and failures

Explicit supported settings commands retain the existing local executor and
permission rules. The model cannot enable notifications, change farms, grant
location permission, send SMS or claim an action completed. Native setup controls
continue to own location and notification consent.

The conversation reserves its durable request before any generation. Reopening,
refreshing and replaying the same completed request do not call the model again.
An uncertain unfinished request retains the existing reconciliation flow.

Accepted replies retain `generation=chatgpt_plan`, `generation_outcome=completed`,
focus, cited source facts and source/history identities. Reaction/silence decisions
retain equivalent request-level metadata. Invalid, disconnected, paused or failed
plan generation uses a local factual fallback with truthful template metadata.
Fallback never retries the provider or silently switches to API-key billing.

Default runtime writers use this engine automatically when ChatGPT is enabled.
Explicitly injected/custom writers and the legacy orchard-question interface
retain their existing contracts. A simulation connected through the ordinary
runtime reuses its engine writer and protected plan connection while retaining
the simulation's separate journals, source guards and disabled external SMS.

## Verification

Focused tests exercise contextual follow-ups, focused questions, unfamiliar crop
answers, no default crop, source separation, grounding rejection, guarded HTTP
routing through the actual runtime wrapper, durable retries, quiet reopening,
local action execution, proactive interpretation, mixed-state event validation
and one-call fallback. These are controlled-provider tests. Genuine plan proof
must separately record a completed grounded question, an elliptical follow-up and
a necessary clarification through the operator-owned Terra connection on the
final integrated source. Never substitute Codex or browser credentials.

The transport preserves the supported [ChatGPT-plan Responses requirements](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations):
context is supplied in `input`, `store` is false, streaming is enabled, and a
completed response is required. No new server-side conversation store is used.
