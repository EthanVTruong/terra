# Production UI with controlled historical weather

This opt-in QA process serves the **actual built production Terra interface** at
`/` and `/terra`, and the existing station lab at `/stations`. They use one
isolated replay journal, real frozen QC, Nathan's estimator and the real orchard
monitor. It does not change the normal service or the port-8520 demonstration.

Run only after building the desired production UI and `web/demo`. From the
checkout, with its installed requirements and `PYTHONPATH=backend`:

```powershell
python -m terra_monitoring.demo_simulation.full_flow --port 8530 `
  --state-dir C:/private/terra-full-flow-qa `
  --web-root C:/path/to/checkout/web/dist/client `
  --demo-root C:/path/to/checkout/web/demo/dist/client
```

The default host is literal loopback. No environment files, account vaults,
weather acquisition, SMS dispatcher, language model or worker are configured.
The writer uses the native factual fallback. The private state directory must be
new or already marked as this full-flow process's own journal. Existing demo and
live journals are rejected, rather than adopted or reset.

## What the walkthrough establishes

1. In `/terra`, set up a farm using the actual interface available in that build.
   New QA profiles are forced to **injected** mode, retain the supplied geometry,
   and cannot opt in to real SMS. After the Tara onboarding/profile integration
   is present, its native conversation router attaches automatically and preserves
   supplied crops and variety. Until then, the older coordinate form works but
   cannot supply crop metadata; crop-aware calls fail explicitly.
   If the production factory already attached its conversation service and
   routes, the bridge reuses them. An explicit `conversation_factory` is a
   fallback only and never replaces that native instance.
2. The farm receives a real baseline assessment from the saved March observations.
   The observation clock remains historical. A usable replay comparison does
   **not** imply live monitoring readiness: no fresh worker heartbeat is created.
3. Open `/stations` on the same port. Original/minor adjustments stay quiet when
   usable peers support the farm. A station spike runs the actual detector and
   remains diagnostic when independent support is sufficient.
4. Choose **Support loss**. Each saved user farm is assessed at its own geometry;
   `Monitor.apply` and `flush` publish its actual coverage note. The production
   inbox reads those same persisted messages and exact immutable Details.
5. Repeating an unchanged hour does not duplicate the opening or prove recovery.
   **Restore +3h** evaluates three distinct subsequent hours for every QA farm.
   The actual monitor decides whether recovery is established.
6. Read the older loss note after recovery. Its original assessment is retained.
   Follow-ups use the native conversation service when integrated, with no model
   configured. Optional browser alerts describe open-page delivery only.

External delivery remains disabled even if the host has credentials configured.
The station lab's phone card is labelled simulation; it is not an SMS receipt.
Current-weather context is explicitly unconfigured, so saved replay cannot be
presented as current provider weather. No real account connection is supplied.
The station lab retains its existing public OpenStreetMap basemap dependency in
the browser. Those visual tile requests are separate from the offline saved
weather, numerical assessment, monitor and message paths.

## Isolation and replay controls

The original core station-demo profile cannot be replaced through the production
API. It is also hidden from the production orchard list and native conversation
state: a fresh grower visit starts with no farm and no seeded demo welcome. The
station lab retains its original profile and messages in the same underlying
journal. Saved user profiles are immutable, as in the native store. **Original**
restores current-hour station controls. After saving a user farm, the entire-run
Reset action is refused: moving the observation clock backwards would contaminate
that farm's causal history. Start a new process with a new state directory for a
fresh complete walkthrough. Retain the previous directory as evidence.

Production assets remain under `/assets`; station-lab assets and its font use
`/demo-assets` and `/demo-static`, preventing filename collisions. The bridge
does not rewrite production UI source or any numerical scientific policy.

Focused verification: `tests/test_terra_full_flow.py` exercises native HTTP/feed/
Details paths with real saved weather, outage/recovery/deduplication, isolation,
same-origin guards and colliding asset names. Existing demo tests remain useful
for its own two-view experience. Browser verification must separately identify
the exact integrated UI revision, real vs simulated permissions and pending
monitor readiness; passing HTTP tests alone does not establish the full UX.
