# Prototype Instructions

## Accepted Terra simulation direction

- Separate local Station lab and Terra grower-chat views share an isolated demo backend. Keep the ordinary live service unchanged.
- Match the inspected Grok Bot conversation primitives directly: bundled xVf font, 44px header/composer, #fcfcfc surface, #eee 14/20 incoming bubbles, 8/12px insets and 16/16/16/6px corners. Terra content and evidence replace the source roster/workspace; do not add unrelated Grok features.
- ET selected an actual orchard-support alert: one flagged station may remain diagnostic while independent peers support the orchard. Lost support creates the proactive note. Never manufacture an orchard alert for slider movement.
- Actual immutable March 2026 CIMIS observations are outside frozen QC training and calibration. Display controlled modifications and historical time honestly. Previously inspected data is not a new accuracy evaluation.
- Use the real frozen QC, Nathan estimator and orchard Monitor. Browser code displays their results. Heatmap colors represent temperature, separate from explicit backend flag status.
- Keep controls and copy lean. Evidence stays behind the note, source disclosure and support/temperature tabs. Phone pings are local simulations from the actual persisted note identity and text; no SMS/provider/LLM calls.
- Each older note keeps its first-publication station values and chart. Never replace historical evidence with current controls. Recovery uses three distinct usable observation hours.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
