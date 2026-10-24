# Terra UI fidelity pass — 2026-10-24

This is a presentation change on the committed current-location assessment app.
The live request, validation, cancellation and exact packet boundaries remain in
place. `POST /api/assess` remains read-only. No detector, estimator, data, provider,
SMS, worker or numerical policy changes are part of this commit.

## Grounding

- Exact Grok Bot source: https://x.ai/news/designing-grok-bot
- Exact Fliptexts source: https://fliptexts.com/
- Approved logo: `web/public/brand/manifest.json`, Canva design `DAHY50X-kKA`.
- Base implementation: `7f37e2670103fc2efa83c272e1cab285e3616620`; the parent
  live API is now merged into shared main at `37948ed`. Root owns final integration.

The current active chats supplied the stable live API, approved brand exports,
incoming-only product policy and the developing monitoring/SMS contracts. Their
unfinished transport/feed implementation is not duplicated by this visual slice.

## Source-backed visual values

The article embeds a 1024 × 768 product demo at half scale. Its 280px roster is
intentionally omitted for Terra. The resulting chat/workspace target is 744 × 768,
with approximately 424px conversation and 320px evidence, a 44px header and 16px
window radius. Incoming prose uses 14px /20px, 8px /12px padding, a 300px maximum
width, corner radii 16/16/16/6, canvas `#fcfcfc`, bubble `#eee`, ink `#141414`.
The source typography asset is retained with its origin under `web/public/fonts`.
Phosphor supplies consistent open icons in place of unpublished product glyphs.

Terra preserves the accepted three-value note and incoming-only conversation.
The pinned evidence pane replaces Grok's computer/routines with the exact
temperature evidence. JSON is one tab away; detailed provenance stays collapsed.
The bottom pill opens actual location entry; its plus opens saved assessments.
No decorative roster, voice control or nonfunctional message composer is added.

## Interaction and motion evidence

| Mechanism | Evidence and application |
| --- | --- |
| Grok widget carousel | Direct click changes the demonstrated widget. Source CSS specifies 350ms and `cubic-bezier(.25,.8,.35,1)` for card/strip transitions. This curve is reused. |
| Grok window controls | Static article reconstruction; native open/close/focus timings remain unknown. |
| Fliptexts | Public landing page demonstrates short incoming bubbles and typing feedback; the dashboard is a published image. No authenticated dashboard behavior is claimed. CSS exposes 150ms hover opacity and 560ms ease-out institution transitions. |
| Terra evidence pane | Proposed 220ms grid transition; retains selected tab; a note opens its matching temperature evidence, attachment opens JSON. Mobile slides to a dialog, traps Tab, supports Escape and restores the actual opener. |
| Terra source/location popovers | Proposed 180ms transform /160ms opacity, anchored above the pill; Escape/outside click dismiss, draft persists, close returns focus. Actual requests acknowledge immediately with no artificial wait. |
| Terra disclosures and feedback | Proposed 200ms disclosure size and 180ms message arrival. Copy feedback expires and is tied to the exact packet. Charts render observations directly without animated numerical interpolation. |
| Reduced motion | CSS removes transitions/animations including disclosure pseudo-elements. OS preference switching was not available through the inspected browser controls. |

All timing values above are source-backed CSS or explicitly proposed. Tool-call
elapsed time was not used as an animation measurement.

## Factual and connected behavior

- Three readings retain backend values; unavailable values stay unavailable.
- The peer list corresponds to the selected evidence time, including a distinct
  peak time. Current station comparisons retain their matching evidence when present.
- Historical/injected labels and the evidence date remain visible in the mobile
  evidence pane. Saved current-weather packets do not become new provider queries.
- Reading and chart labels share Pacific time; source JSON keeps original timestamps.
- Query success restores focus after the busy button disables. Failure/cancellation
  retains the last successful result and explicitly labels it.
- Chart code loads separately, preserving a small initial conversation path.

Local screenshots and the combined source/implementation comparison are under
`.terra-local/ui-fidelity/`. The frontend QA report is `web/design-qa.md`.
