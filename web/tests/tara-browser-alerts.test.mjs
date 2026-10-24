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

test("an existing completed farm can request explicit browser consent and retain a denied choice", { skip: !browserUrl }, async () => {
  const url = new URL(browserUrl);
  assert.ok(["127.0.0.1", "localhost", "::1"].includes(url.hostname));
  const require = createRequire(import.meta.url);
  const { chromium } = require(process.env.TERRA_PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch({ channel: process.env.TERRA_BROWSER_CHANNEL || "msedge", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => {
      window.__permissionRequests = 0;
      class TestNotification {
        static permission = "default";
        static async requestPermission() { window.__permissionRequests += 1; this.permission = "denied"; return "denied"; }
      }
      window.Notification = TestNotification;
      localStorage.setItem("terra.tara.activeFarm", "existing-farm");
    });
    const profile = { id: "existing-farm", name: "Existing Orchard", mode: "current", crop: "almond" };
    const turn = (id, role, text, data = {}, kind = "text") => ({ id, role, text, kind, data, created_at: "2026-10-24T16:00:00Z", reply_to: null, reactions: [], delivery: "local_saved" });
    const snapshot = { schema_version: "terra-conversation-v1", setup: { stage: "complete", crops: ["almond"] }, orchard_id: profile.id, orchards: [profile], turns: [turn("prior-choice", "event", "In-chat updates only", { saved: true, enabled: false }, "notification")], pending_requests: [], initial_prompt: null, monitoring: { status: "pending" }, notifications: { enabled: false, permission: "default", capability: "browser_open", scope: "while_open" } };
    const setupPosts = [];
    await page.route("**/api/**", async (route) => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === "POST" && path === "/api/tara/messages") {
        const body = request.postDataJSON();
        assert.equal(body.orchard_id, profile.id);
        const user = turn(`${body.request_id}:user`, "user", body.text);
        const assistant = turn(`${body.request_id}:assistant`, "assistant", "Use the notification choice to enable browser alerts while Terra is open.", { action: "notification_choice" });
        snapshot.turns.push(user, assistant);
        return route.fulfill({ json: { request_id: body.request_id, outcome: "message", user_turn: user, assistant_turn: assistant, reaction: null, state: snapshot } });
      }
      if (request.method() === "POST" && path === "/api/tara/setup") {
        const body = request.postDataJSON();
        setupPosts.push(body);
        assert.equal(body.orchard_id, profile.id);
        assert.equal(body.action, "notifications");
        snapshot.notifications = { ...snapshot.notifications, ...body.notifications };
        const event = turn(`${body.request_id}:event`, "event", "In-chat updates only", { ...body.notifications, saved: true }, "notification");
        snapshot.turns.push(event);
        return route.fulfill({ json: { request_id: body.request_id, outcome: "none", user_turn: null, assistant_turn: null, reaction: null, event, state: snapshot } });
      }
      if (path === "/api/tara/conversation") return route.fulfill({ json: snapshot });
      if (path === "/api/orchards") return route.fulfill({ json: { orchards: [profile] } });
      if (path === "/api/orchards/existing-farm/messages") return route.fulfill({ json: { schema_version: "terra-farm-feed-v1", orchard_id: profile.id, messages: [], monitor: { orchard: profile } } });
      if (path === "/api/context") return route.fulfill({ json: { schema_version: "terra-live-context-v1", configured: false, source: "CIMIS", station_presets: [] } });
      if (path === "/api/sms/status") return route.fulfill({ json: { configured: false, enabled: false, demo_enabled: false, control_token: "local-browser-proof" } });
      return route.fulfill({ status: 404, json: { error: { code: "not_found" } } });
    });
    await page.goto(url.href);
    const field = page.getByRole("textbox", { name: "Message Tara", exact: true });
    await field.waitFor();
    assert.equal(await page.getByRole("button", { name: "Enable browser alerts", exact: true }).count(), 0);
    await field.fill("Enable browser alerts");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    const consent = page.getByRole("button", { name: "Enable browser alerts", exact: true });
    await consent.waitFor();
    assert.equal(await page.evaluate(() => window.__permissionRequests), 0);
    assert.equal(setupPosts.length, 0);
    assert.equal(snapshot.setup.stage, "complete");
    const saved = page.waitForResponse((response) => response.url().endsWith("/api/tara/setup") && response.request().method() === "POST");
    await consent.click();
    assert.equal((await saved).status(), 200);
    await page.waitForFunction(() => !document.querySelector("#tara-message")?.disabled);
    assert.equal(await page.evaluate(() => window.__permissionRequests), 1);
    assert.equal(setupPosts.length, 1);
    assert.deepEqual(setupPosts[0].notifications, { enabled: false, permission: "denied" });
    assert.equal(snapshot.notifications.enabled, false);
    await page.reload();
    await field.waitFor();
    assert.equal(await page.getByRole("button", { name: "Enable browser alerts", exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => window.__permissionRequests), 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
