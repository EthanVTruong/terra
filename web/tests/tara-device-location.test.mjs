import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const browserUrl = process.env.TERRA_BROWSER_URL;
const require = createRequire(import.meta.url);
const stamp = "2026-10-24T16:00:00Z";
const turn = (id, role, text, data = {}, kind = "text") => ({ id, role, text, data, kind, created_at: stamp, reply_to: null, reactions: [], delivery: "local_saved" });

async function fixture({ native = "deferred", errorCode = 1, firstResponse = "success", savedFarm = false } = {}) {
  const url = new URL(browserUrl);
  assert.ok(["127.0.0.1", "localhost", "::1"].includes(url.hostname));
  const { chromium } = require(process.env.TERRA_PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch({ channel: process.env.TERRA_BROWSER_CHANNEL || "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
  const errors = [], posts = [], results = new Map();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(({ mode, code }) => {
    window.__geoCalls = 0; window.__geoMode = mode; window.__geoCode = code;
    const geo = { getCurrentPosition(success, failure, options) {
      window.__geoCalls += 1; window.__geoOptions = options;
      window.__geoSuccess = success; window.__geoFailure = failure;
      if (window.__geoMode === "error") failure({ code: window.__geoCode });
      if (window.__geoMode === "success") success({ coords: { latitude: 38.55, longitude: -121.8, accuracy: 7 } });
    } };
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: mode === "unsupported" ? undefined : geo });
  }, { mode: native, code: errorCode });
  const snapshot = { schema_version: "terra-conversation-v1", setup: { stage: "location", crops: ["almond"], crop_description: "Almonds", variety: null }, orchard_id: null, orchards: [], turns: [turn("location-question", "assistant", "Where are you growing? Share your location.", { action: "location_choice" })], initial_prompt: null, pending_requests: [], monitoring: { status: "not_configured" }, notifications: { enabled: false, permission: "default", capability: "browser_open" } };
  if (savedFarm) snapshot.orchards.push({ id: "existing-farm", name: "Existing farm", crop: "peach", mode: "current" });
  const snapshotCopy = () => structuredClone(snapshot);
  await page.route("**/api/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === "/api/tara/setup" && request.method() === "POST") {
      const body = request.postDataJSON(); posts.push(body);
      assert.equal(body.action, "device_location");
      assert.deepEqual(Object.keys(body.device_location).sort(), ["accuracy_m", "lat", "lon"]);
      if (posts.length === 1 && firstResponse === "lookup_failure") return route.fulfill({ status: 503, json: { error: { code: "location_lookup_failed", retryable: true } } });
      if (posts.length === 1 && firstResponse === "setup_changed") {
        snapshot.setup = { stage: "crop", crops: [] }; snapshot.turns = []; snapshot.initial_prompt = "What are you growing?";
        return route.fulfill({ status: 409, json: { error: { code: "setup_changed", retryable: false } } });
      }
      if (!results.has(body.request_id)) {
        const profile = { id: "shared-farm", name: "Shared location", crop: snapshot.setup.crops[0], mode: "current", lat: body.device_location.lat, lon: body.device_location.lon, elevation_ft: 65 };
        const event = turn(`${body.request_id}:event`, "event", "Location shared", { source: "device", saved: true, confirmed: true, orchard_id: profile.id }, "location");
        const assistant = turn(`${body.request_id}:assistant`, "assistant", "Want a heads-up when something needs attention?", { action: "notification_choice" });
        snapshot.orchard_id = profile.id; snapshot.orchards = [profile]; snapshot.setup = { ...snapshot.setup, stage: "notifications", orchard_id: profile.id }; snapshot.monitoring.status = "pending"; snapshot.turns.push(event, assistant);
        results.set(body.request_id, { request_id: body.request_id, outcome: "message", user_turn: null, assistant_turn: assistant, reaction: null, event, orchard_id: profile.id, state: snapshotCopy() });
      }
      if (posts.length === 1 && firstResponse === "lost") return route.abort();
      return route.fulfill({ json: results.get(body.request_id) });
    }
    if (path === "/api/tara/conversation") {
      const requested = new URL(request.url()).searchParams.get("orchard_id");
      if (savedFarm && requested === "existing-farm") return route.fulfill({ json: { ...snapshotCopy(), orchard_id: requested, setup: { stage: "complete", crops: ["peach"], orchard_id: requested }, turns: [] } });
      return route.fulfill({ json: snapshotCopy() });
    }
    if (path === "/api/orchards") return route.fulfill({ json: { orchards: snapshot.orchards } });
    if (/^\/api\/orchards\/[^/]+\/messages$/.test(path)) {
      const requested = path.split("/")[3];
      return route.fulfill({ json: { schema_version: "terra-farm-feed-v1", orchard_id: requested, messages: [], monitor: { orchard: snapshot.orchards.find((item) => item.id === requested) } } });
    }
    if (path === "/api/context") return route.fulfill({ json: { schema_version: "terra-live-context-v1", configured: false, source: "CIMIS", station_presets: [{ station_id: 6, name: "Davis", lat: 38.5, lon: -121.7, elevation_ft: 60 }] } });
    if (path === "/api/sms/status") return route.fulfill({ json: { configured: false, enabled: false, demo_enabled: false, control_token: "device-location-browser-proof" } });
    return route.fulfill({ status: 404, json: { error: { code: "not_found" } } });
  });
  await page.goto(url.href);
  await page.getByRole("button", { name: "Use my location", exact: true }).waitFor();
  const noForm = async () => {
    assert.equal(await page.locator(".tara-setup-controls input, .tara-setup-controls select, .tara-setup-controls form").count(), 0);
    assert.equal(await page.getByRole("button", { name: "Enter coordinates", exact: true }).count(), 0);
    assert.equal(await page.getByLabel("Known station reference", { exact: true }).count(), 0);
  };
  return { browser, page, snapshot, posts, errors, noForm };
}

test("one location button requests fresh native position once and automatically advances without altitude or form fields", { skip: !browserUrl }, async () => {
  const f = await fixture();
  try {
    await f.noForm();
    if (process.env.TERRA_DEVICE_SCREENSHOT) await f.page.screenshot({ path: process.env.TERRA_DEVICE_SCREENSHOT, fullPage: true, animations: "disabled" });
    assert.equal(await f.page.evaluate(() => window.__geoCalls), 0);
    await f.page.getByRole("button", { name: "Use my location", exact: true }).evaluate((button) => { button.click(); button.click(); });
    assert.equal(await f.page.evaluate(() => window.__geoCalls), 1);
    assert.deepEqual(await f.page.evaluate(() => window.__geoOptions), { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
    assert.equal(f.posts.length, 0);
    await f.page.evaluate(() => { const position = { coords: { latitude: 38.55, longitude: -121.8, accuracy: 7 } }; window.__geoSuccess(position); window.__geoSuccess(position); });
    await f.page.getByRole("button", { name: "Keep in chat", exact: true }).waitFor();
    assert.equal(f.posts.length, 1);
    assert.deepEqual(f.posts[0].device_location, { lat: 38.55, lon: -121.8, accuracy_m: 7 });
    await f.page.getByText("Location shared", { exact: true }).waitFor();
    assert.equal(f.snapshot.setup.stage, "notifications");
    await f.noForm();
    assert.equal(await f.page.evaluate(() => localStorage.getItem("terra.tara.v1.location")), null);
    await f.page.setViewportSize({ width: 320, height: 800 });
    const size = await f.page.locator("body").evaluate((node) => ({ width: node.clientWidth, scroll: node.scrollWidth }));
    assert.ok(size.scroll <= size.width + 1);
    if (process.env.TERRA_DEVICE_SUCCESS_SCREENSHOT) await f.page.screenshot({ path: process.env.TERRA_DEVICE_SUCCESS_SCREENSHOT, fullPage: true, animations: "disabled" });
    assert.deepEqual(f.errors, []);
  } finally { await f.browser.close(); }
});

for (const [name, native, errorCode, message] of [
  ["denial", "error", 1, "Location wasn’t shared. Allow location in your browser settings, then try again."],
  ["timeout", "error", 3, "Location took too long. Try again."],
  ["unsupported browser", "unsupported", 0, "This browser can’t share location. Use a browser with location access."],
]) test(`${name} keeps the single location action with concise help and makes no setup request`, { skip: !browserUrl }, async () => {
  const f = await fixture({ native, errorCode });
  try {
    await f.page.getByRole("button", { name: "Use my location", exact: true }).click();
    await f.page.getByText(message, { exact: true }).waitFor();
    assert.equal(f.posts.length, 0); await f.noForm();
    assert.equal(await f.page.getByRole("button", { name: "Use my location", exact: true }).isEnabled(), true);
    if (native === "error") {
      await f.page.evaluate(() => { window.__geoMode = "success"; });
      await f.page.getByRole("button", { name: "Use my location", exact: true }).click();
      await f.page.getByRole("button", { name: "Keep in chat", exact: true }).waitFor();
      assert.equal(f.posts.length, 1);
    }
    assert.deepEqual(f.errors, []);
  } finally { await f.browser.close(); }
});

for (const firstResponse of ["lost", "lookup_failure"]) test(`${firstResponse} retries the exact approved location and request ID after reload without requesting native location again`, { skip: !browserUrl }, async () => {
  const f = await fixture({ native: "success", firstResponse });
  try {
    await f.page.getByRole("button", { name: "Use my location", exact: true }).click();
    await f.page.getByRole("button", { name: "Retry location", exact: true }).waitFor();
    if (firstResponse === "lookup_failure") await f.page.getByText("Couldn’t get your location details. Try again.", { exact: true }).waitFor();
    const original = structuredClone(f.posts[0]);
    assert.equal(await f.page.evaluate(() => window.__geoCalls), 1);
    await f.page.reload();
    await f.page.getByRole("button", { name: "Retry location", exact: true }).click();
    await f.page.getByRole("button", { name: "Keep in chat", exact: true }).waitFor();
    assert.equal(f.posts.length, 2); assert.deepEqual(f.posts[1], original);
    assert.equal(await f.page.evaluate(() => window.__geoCalls), 0);
    assert.equal(f.snapshot.turns.filter((item) => item.kind === "location").length, 1);
    assert.deepEqual(f.errors, []);
  } finally { await f.browser.close(); }
});

for (const change of ["setup", "farm"]) test(`a late native callback cannot save after the ${change} changes`, { skip: !browserUrl }, async () => {
  const f = await fixture({ savedFarm: change === "farm" });
  try {
    await f.page.getByRole("button", { name: "Use my location", exact: true }).click();
    if (change === "setup") {
      f.snapshot.setup = { stage: "location", crops: ["peach"], crop_description: "Peaches" };
      f.snapshot.turns.push(turn("replacement-prompt", "assistant", "Share the location for your peaches.", { action: "location_choice" }));
    } else await f.page.getByLabel("Conversation farm", { exact: true }).selectOption("existing-farm");
    if (change === "setup") await f.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    if (change === "setup") await f.page.getByText("Share the location for your peaches.", { exact: true }).waitFor();
    else await f.page.locator(".tara-monitoring").waitFor();
    await f.page.evaluate(() => window.__geoSuccess({ coords: { latitude: 38.55, longitude: -121.8 } }));
    await f.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(f.posts.length, 0); assert.deepEqual(f.errors, []);
    if (change === "farm") assert.equal(f.snapshot.setup.stage, "location");
  } finally { await f.browser.close(); }
});

test("a server-detected changed setup discards only the unused location request and refreshes instead of retrying it", { skip: !browserUrl }, async () => {
  const f = await fixture({ native: "success", firstResponse: "setup_changed" });
  try {
    await f.page.getByRole("button", { name: "Use my location", exact: true }).click();
    await f.page.getByText("What are you growing?", { exact: true }).waitFor();
    assert.equal(f.posts.length, 1);
    assert.equal(await f.page.getByRole("button", { name: "Retry location", exact: true }).count(), 0);
    assert.equal(await f.page.evaluate(() => localStorage.getItem("terra.tara.v1.setup.pending")), null);
    assert.deepEqual(f.errors, []);
  } finally { await f.browser.close(); }
});
