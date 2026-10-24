import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

// Run against this checkout's dev server; browser dependencies stay optional.
// TERRA_BROWSER_URL=http://127.0.0.1:8519
// TERRA_PLAYWRIGHT_MODULE=/absolute/path/to/playwright (when not installed locally)
const browserUrl = process.env.TERRA_BROWSER_URL;
test("enabling browser alerts excludes a delayed pre-consent note and alerts once for a genuinely new note", { skip: !browserUrl }, async () => {
  const url = new URL(browserUrl);
  assert.ok(["127.0.0.1", "localhost", "::1"].includes(url.hostname), "Use an isolated local preview");
  const require = createRequire(import.meta.url);
  const { chromium } = require(process.env.TERRA_PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch({ channel: process.env.TERRA_BROWSER_CHANNEL || "msedge", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => {
      window.__taraNotifications = [];
      class TestNotification {
        constructor(title, options) { window.__taraNotifications.push({ title, options }); }
        static permission = "granted";
        static async requestPermission() { return "granted"; }
      }
      window.Notification = TestNotification;
      localStorage.setItem("terra.tara.activeFarm", "farm-1");
    });
    const profile = { id: "farm-1", name: "Test Orchard", mode: "current", crop: "almond" };
    const snapshot = {
      schema_version: "terra-conversation-v1", setup: { stage: "complete", crops: ["almond"] },
      orchard_id: profile.id, orchards: [profile], turns: [], pending_requests: [], initial_prompt: null,
      monitoring: { status: "pending" }, notifications: { enabled: false, capability: "browser_open", scope: "while_open" },
    };
    const note = (id, text, created_at) => ({ id, text, created_at, orchard_id: profile.id, kind: "proactive" });
    const initial = note("A", "Existing initial alert", "2026-10-24T16:00:00Z");
    const delayed = note("B", "Delayed alert published while off", "2026-10-24T16:01:00Z");
    const fresh = note("C", "New alert published after consent", "2026-10-24T16:03:00Z");
    let notes = [initial];
    const monitor = { orchard: profile, comparison_available: false, source_error: null, worker: { heartbeat_status: "fresh" } };
    await page.route("**/api/**", async (route) => {
      const request = route.request(), path = new URL(request.url()).pathname;
      assert.equal(request.method(), "GET", "Opening and refreshing the feed must not mutate state");
      if (path === "/api/tara/conversation") return route.fulfill({ json: snapshot });
      if (path === "/api/orchards") return route.fulfill({ json: { orchards: [profile] } });
      if (path === "/api/orchards/farm-1/messages") return route.fulfill({ json: { schema_version: "terra-farm-feed-v1", orchard_id: profile.id, messages: notes, monitor } });
      if (path === "/api/context") return route.fulfill({ json: { schema_version: "terra-live-context-v1", configured: false, source: "CIMIS", station_presets: [] } });
      if (path === "/api/sms/status") return route.fulfill({ json: { configured: false, enabled: false, demo_enabled: false, control_token: "local-browser-proof" } });
      return route.fulfill({ status: 404, json: { error: { code: "not_found" } } });
    });
    const flushEffects = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const refresh = () => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    const count = () => page.evaluate(() => window.__taraNotifications.length);
    await page.goto(url.href);
    await page.getByText(initial.text, { exact: true }).waitFor();
    await flushEffects();
    assert.equal(await count(), 0);

    // Consent changes while the feed still contains only A. B already exists
    // on the server, but its delayed response arrives after the saved choice.
    snapshot.notifications.enabled = true;
    snapshot.turns.push({ id: "enable-event", role: "event", kind: "notification", text: "Browser notifications enabled", created_at: "2026-10-24T16:02:00Z", reply_to: null, reactions: [], delivery: "local_saved", data: { enabled: true, saved: true, permission: "granted" } });
    await refresh();
    await page.getByText("Browser notifications enabled", { exact: true }).waitFor();
    await flushEffects();
    notes = [initial, delayed];
    await refresh();
    await page.getByText(delayed.text, { exact: true }).waitFor();
    await flushEffects();
    assert.equal(await count(), 0, "A delayed pre-consent note must stay quiet");
    notes = [initial, delayed, fresh];
    await refresh();
    await page.getByText(fresh.text, { exact: true }).waitFor();
    await flushEffects();
    assert.equal(await count(), 1, "Only the genuinely new note should notify");
    await refresh();
    await flushEffects();
    assert.equal(await count(), 1, "Repeated feed reads must stay quiet");
    await page.reload();
    await page.getByText(fresh.text, { exact: true }).waitFor();
    await flushEffects();
    assert.equal(await count(), 0, "Reopening must only establish the baseline");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
