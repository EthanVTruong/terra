# Orchard-support demonstration

This separate local application has two synchronized views:

- `/stations`: manual temperature and reporting controls, plus raw and Terra-filtered temperature heatmaps.
- `/terra`: the source-grounded Grok Bot style grower conversation, factual questions, retained note evidence and a simulated phone notification.

Both use the same isolated server on loopback port 8520. No changes enter the ordinary live monitor or its journals. No weather provider, model fitting, LLM or SMS transport runs. The optional basemap is attributed Esri Light Gray Canvas browser tiles (base and place-name layers, without point-of-interest icons); all numerical evidence and computation remain local and work without those tiles.

## Run

Use an environment containing `requirements-terra-dev.txt` and the existing Node/pnpm setup:

```powershell
pnpm --dir web/demo install --frozen-lockfile
pnpm --dir web/demo build
$env:PYTHONPATH = 'backend'
python -m terra_monitoring.demo_simulation --host 127.0.0.1 --port 8520 --state-dir .terra-local/demo-session
```

The two browser URLs use the same origin and session. The application persists the demo in its own `demo.sqlite3`; reset starts a new isolated orchard/run and retains past records. The server has no live source, delivery dispatcher or enabled generated writer. Its port differs from the ordinary runtime.

## Demonstrate

1. **Original / Small change:** usable weather support and no new proactive note.
2. **Temperature slider / Station spike:** actual frozen station QC detects the injected departure. The raw heatmap changes; the filtered map uses Nathan's estimator with Terra's exclusions. Adequate peers can preserve a usable orchard estimate, correctly producing no orchard alert.
3. **Reporting toggles / Support loss:** insufficient usable inputs make the actual orchard Monitor record `monitoring_limited`. The same persisted note ID and original text appear in the grower chat and local simulated phone toast. Repeating an unchanged incident does not create another notification.
4. **Select the note:** retained support, station values and chart come from first publication. Later changes do not rewrite them. Ask “Why did I get this alert?” for the Monitor's factual explanation; answers do not create proactive phone notifications.
5. **Restore:** Original restores readings in the current hour. The last unavailable hour remains the incident boundary; revising it does not count toward recovery. Advance through three subsequent usable hourly estimates, or use **Restore +3h**. The backend records one recovery note only after its actual rule is satisfied.

Temperature overrides apply to one hourly frame. Reporting interruptions persist until restored. Time advances only through the controls, using actual next observations; the active slice has a finite endpoint. The clock is fixed PST, matching the detector's convention. A changed hour remains historical replay, never current weather.

## Data and visual provenance

The committed [source manifest](demo-simulation/data/provenance.json) records the immutable 840-row March 9–15, 2026 normalized CIMIS source and its SHA-256. Active time begins March 11 at 11:00 UTC (3 AM PST), with earlier held-out hours for causal warmup and 17 later real hours available. Every source row is after training ending September 1, 2025 and calibration ending December 1, 2025. Original quality flags and raw provider fields are preserved; overrides affect working copies only. This is already inspected demonstration data, not an untouched accuracy test.

The selected reference remains `terra-qc-v4-e1e59ac0ea395d80`, SHA-256 `809c78b69d80d93d73f5b4b75ca6e74b0759ddcebf1e17fa2450386b44fcd90f`. No scientific module or reference is changed. Each heatmap sample calls Nathan's public `estimate_location` at an explicitly fixed 60-foot demonstration elevation; canvas smoothing is only display interpolation. The view preserves geographic proportions and marks unavailable estimates without invented temperatures.

The visual reference is the actual DOM-rendered example in [Designing Grok Bot](https://x.ai/news/designing-grok-bot). The original reference review used xVf (SHA-256 `6832bfa9850af0226f5e8072d5813ec703c0104f13df89b52b6f195010cb65d3`); the open-source release uses the existing system sans-serif fallback because no font redistribution grant was recorded. Historical comparison captures retain their original typography. Conversation geometry and bubble tokens follow the inspected source; the absent bot roster, station controls, map, grower notes and evidence are deliberate Terra adaptations. Phosphor icons use the existing open icon library.

Verification and comparison evidence are in [the app's QA report](../../web/demo/design-qa.md). Tests run real frozen QC/estimation/monitor transitions, source immutability, retained old evidence, replay reset/restart, local API isolation and native heatmap sample parity. Browser checks cover the actual two-view sequence and responsive behavior. No simulated phone state is evidence of SMS delivery.
