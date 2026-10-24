# Terra simulation verification

Typography note: the captures and exact-font comparisons below describe the
historical reference build. The open-source release uses the existing system
sans-serif fallback and no longer distributes the xVf font. Layout sizes and
spacing are retained; current cross-platform visual verification is separate.


Date: 2026-10-24. Local monitoring baseline: `e4292b21594d20b3983a62447e2f4632aac0f3bd`.

## Visual truth and normalization

Source: the DOM-rendered light desktop conversation in [Designing Grok Bot](https://x.ai/news/designing-grok-bot). Source capture: `../../docs/terra/demo-simulation/evidence/grok-source-desktop.png` (1280×720 pixels). The embedded source window is 1024×768 CSS pixels, displayed at 0.5 scale; its 512×384 screenshot crop was enlarged 2× for native-scale comparison. Source screenshot blur from that enlargement is not a font mismatch.

Implementation: `http://127.0.0.1:8520/terra` and `/stations`. Final native chat-frame capture: `../../docs/terra/demo-simulation/evidence/terra-frame-final.png`, exactly 744×615 pixels at a 1280×720 CSS viewport. Browser DOM measured 744px frame width, 44px headers/composer, and 14px/20px incoming text with 8px/12px padding, 300px maximum width and 16/16/16/6px corners. The xVf source font is loaded, with file SHA-256 `6832bfa9850af0226f5e8072d5813ec703c0104f13df89b52b6f195010cb65d3`.

Full-view comparison: `../../docs/terra/demo-simulation/evidence/source-implementation-comparison-final.png`. Focused native-scale chat comparison: `../../docs/terra/demo-simulation/evidence/source-chat-focus-comparison-final.png`. Both contain the source and rendered implementation in the same comparison input.

Accepted adaptations: omit the unrelated bot roster; retain a roughly 423px conversation and 320px evidence pane in the compact Terra window. Grower notes, the temperature/support evidence, questions and the separate station simulator replace source-specific content. Source paper/bubble/ink tokens and typography are retained. Selected-note feedback and readable muted labels are deliberate interaction/accessibility adaptations; native Grok timing and uninspected mobile behavior are not claimed.

## Findings and comparison history

No remaining P0/P1/P2 visual findings. An independent reviewer inspected the combined source comparisons and the actual station/chat captures.

1. Initial P2: a filtered map retained the raw hot reading without making exclusion explicit. Fixed with **Excluded from field** on the pin and the selected-reading status. Evidence: `station-spike-filtered-final.png` and its raw counterpart.
2. Initial P2: essential labels were too pale for presentation. Fixed with `#737373` for meaningful secondary labels, controls, timestamps and state explanations; retained source ink/bubble surfaces. The final combined comparison and filtered screenshot verify the change.
3. Responsive P2: the 320px station controls clipped reporting toggles and the support count. Fixed by stacking controls below 390px. Evidence: `station-mobile-320-final.png`; browser bounds confirmed all five 30×24px toggles inside the panel and document width exactly 320px. Only the selected map caption remains visible at small widths, preventing label collisions.
4. Functional polish: initial transcript scroll is instant; subsequent updates preserve a reader's position away from the bottom. Mobile evidence uses a focus-contained dialog; Escape closes it and returns focus to its actual opener. Pending temperature edits show Checking instead of an old classification, and reset/context commands cancel unsubmitted input.

## Required fidelity surfaces

- **Fonts/typography:** exact bundled source font, loaded in-browser; source 14/20 prose and weights retained. Numerical evidence remains a deliberate Terra adaptation.
- **Spacing/layout:** source bubble insets/corners, 44px bars and compact pane proportions verified in the combined full/focused captures. Desktop controls fit the app; narrow layouts scroll without clipping essential controls.
- **Colors/tokens:** `#fcfcfc` paper, `#eeeeee` incoming bubbles and `#141414` ink. Readable neutral secondary labels; heat color encodes temperature, while an explicit label/icon encodes the actual QC finding.
- **Image/assets/icons:** the supplied font and real Phosphor library are used. The basemap is attributed OpenStreetMap imagery. Heatmaps and charts are data visualizations computed from real observations/native estimates, not illustrative substitutes. No fabricated avatars, imagery or CSS/SVG replacements for source assets.
- **Copy/content:** concise actual persisted grower text; initial welcome hidden from the demo feed to keep the normal state quiet. Source/provenance details remain disclosed. Simulated phone labels never claim provider acceptance or real SMS receipt.

## Functional verification

- Real March 2026 CIMIS observations are byte-preserved and outside both reference training and calibration. Input/reference SHA-256 checks pass after interaction.
- Fourteen meaningful backend tests pass against actual frozen QC, Nathan's estimator, Monitor/Store and deterministic writer. They cover baseline/minor no-alert behavior, genuine injected station finding, usable independent orchard support, support-loss events, deduplication, three subsequent usable-hour recovery, immutable note-time station/history snapshots, restart/reset, API guards, native heatmap parity and no live/provider/SMS/LLM side effects.
- All seven configured check groups pass with `python terra.py check --base e4292b21594d20b3983a62447e2f4632aac0f3bd --task terra-demo-simulation`. Broad historical task-overlap warnings do not represent a source conflict: changes remain in the additive simulation paths. Existing scientific modules/reference/configuration match that baseline.
- Production build and all four Sites packaging checks pass. Frozen-lockfile installation succeeds in the app's own pnpm workspace. Local UI/API are served together; publication is outside this local simulation.
- Browser exercised manual slider entry, precise numeric input, raw/filtered layers, no orchard false alert for one bad station, reporting/support loss, matching phone note, factual explanation, retained old evidence, reset, recovery, mobile tabs, Escape/focus return and the evidence download. The downloaded JSON matched its original note-time assessment and immutable data source.
- Fresh final browser-session console checks contain no errors or warnings. Source and implementation screenshots were opened and compared directly; build/HTTP health alone were not treated as visual verification.

Remaining limits: optional OpenStreetMap tiles need network access; the numerical simulation does not. The 8×8 heatmap is a display grid at a fixed 60-foot demonstration elevation. Weather changes and phone pings are simulated; no natural sensor-fault accuracy claim or real SMS delivery claim is made. The existing bundle-size advisory is non-blocking for this local demo.

## Implementation checklist

- [x] Separate synchronized station and grower-chat views
- [x] Source-grounded visual primitives and combined comparison
- [x] Real held-out data and unchanged scientific runtime
- [x] Actual proactive orchard event, retained evidence and simulated phone note
- [x] Responsive/keyboard paths and core controls
- [x] Backend, build, packaging and browser verification

final result: passed
