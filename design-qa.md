# ChatGPT entry design QA

final result: passed

The selected visual source is the user-provided Granola welcome attachment
`codex-clipboard-9966a8f2-9c41-4176-831d-f7e7f483f8d5.png` (3600×2262 pixels).
Only its app-owned frame was used: approximately x600–3000, y292–1972, normalized
from 2400×1680 to 1325×927. Mac chrome and desktop wallpaper are intentionally
excluded. The reference pixels remain in the ignored local handoff, not the
open-source repository.

The latest user direction, “clean without so much whitespace”, supersedes the
initial exact Granola card geometry. The final card is intentionally smaller:
560×450 at a 1325×927 CSS viewport, with a 56px logo, 36px serif heading and one
outlined ChatGPT action. The coordinating owner reviewed and accepted the final
desktop and 320px captures. No additional visual direction was chosen.

## Evidence and normalization

Ignored handoff directory: `.terra-local/handoffs/chatgpt-entry-ui/` in the shared
checkout. Final implementation captures are `compact-desktop-1325.png`
(1325×927 pixels/CSS), `compact-mobile-390.png` (390×844) and
`compact-mobile-320.png` (320×740). Density is normalized to 1 pixel per CSS pixel.
`compact-comparison-normalized.png` places the normalized source and the final
desktop capture in one 2650×927 comparison image; both were inspected together.
The single-card content, logo, text and button are legible in the full-resolution
comparison, so a separate micro-detail crop is unnecessary.

The browser's first viewport capture used a stale scale and was discarded; final
PNG dimensions were independently decoded. The 320px DOM card is 288×325, centered,
with no horizontal or vertical screen overflow. The 390px and matched desktop
views also retain all content and controls. Native CUA used available Brave after
the in-app browser and Chrome were unavailable. No real sign-in or private vault
was used. The sole warning came from an unrelated browser extension, not Terra;
no application warning/error was observed.

## Fidelity surfaces and corrections

- **Typography:** system Georgia/Times serif for the heading (36px desktop,
  32/31px mobile); existing system sans for body/UI. No font asset was added.
- **Spacing/layout:** centered white card, thin border and off-white canvas retain
  the source hierarchy. The final compact geometry intentionally replaces the
  first 640×684 card and the mobile 600px minimum. A short-desktop overflow caused
  by outer padding was corrected; mobile cards now size to their content.
- **Colors/tokens:** white, off-white and quiet gray follow the source. Subtitle
  and secondary copy were darkened to `#71746c` for readability on white.
- **Assets:** existing Terra cube, rounded consistently with the current header;
  Phosphor `OpenAiLogo` supplies the ChatGPT mark. No new artwork or hand-drawn
  replacement asset is present.
- **Copy/content:** requested welcome heading/subtitle and a single sign-in
  provider. Other providers, fake terms and the reference's desktop chrome are
  omitted by explicit scope. Connecting, cancellation, identity-only consent,
  real catalog choice, usage pause, errors and expired-source recovery remain
  honest extensions of the working flow.

No actionable P0/P1/P2 visual issue remains. The earlier overflow and hard-square
logo were corrected before the compact round. Final compact captures, rather
than the earlier full-height captures, are the current design evidence.

## Functional verification boundary

One extracted existing controller owns OAuth/status/catalog/model/usage state.
The opening screen and settings reuse it. CUA controlled API proof verified the
connect route and invalid authorization-link handling, cancellation/focus return,
explicit model selection, entry into chat, shared settings/focus return, and
expired simulation → guarded disconnect → fresh document → normal sign-in.
The expired document's source identity is never silently replaced in place.

Focused final readiness/recovery tests pass, including a catalog that invalidates
a saved model; it closes the gate and reconciles authoritative status without
inference. The production build and four packaging checks pass. The broader UI
suite passed 61 cases with three optional native tests unconfigured before that
last focused repair; those skips are not passes. Existing device-location and
browser-alert fixtures now explicitly supply an eligible account/catalog/source,
with their original assertions retained. New optional native coverage exercises
connect/cancel, real model selection, quiet restore, usage resume and expired-source
recovery. Final assembled CI/browser verification belongs to the sole integrator.
