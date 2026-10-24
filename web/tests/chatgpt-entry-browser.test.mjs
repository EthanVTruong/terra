import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import { eligibleChatGPTFixture } from "./chatgpt-fixture.mjs";

const browserUrl = process.env.TERRA_BROWSER_URL;
const require = createRequire(import.meta.url);
const disconnected = () => ({ schema_version: "terra-chatgpt-connection-v1", state: "disconnected", plan_enabled: false,
  active_account_id: null, accounts: [], writer: { connected: false, active_account_id: null, selected_model: null, usage_paused: false, cooldown_until: null } });

test("one shared entry flow connects, cancels, chooses a real model and restores without inference", { skip: !browserUrl }, async () => {
  const url = new URL(browserUrl);
  assert.ok(["127.0.0.1", "localhost", "::1"].includes(url.hostname));
  const { chromium } = require(process.env.TERRA_PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch({ channel: process.env.TERRA_BROWSER_CHANNEL || "msedge", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1325, height: 927 } });
    const errors = [], writes = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => {
      window.__authLinks = [];
      window.open = () => ({ closed: false, opener: null, location: { replace(url) { window.__authLinks.push(url); } }, close() { this.closed = true; } });
    });
    let status = disconnected(), sourceExpired = false;
    await page.route("**/api/**", async (route) => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() !== "GET") writes.push({ path, method: request.method(), body: request.postData() ? request.postDataJSON() : null });
      if (path === "/api/simulation/connection" && request.method() === "DELETE") {
        assert.equal(sourceExpired, true);
        assert.equal(request.headers()["x-terra-sms-control"], "controlled-entry");
        sourceExpired = false; status = disconnected(); return route.fulfill({ json: { connected: false } });
      }
      if (path === "/api/simulation/connection") return route.fulfill({ json: { ...eligibleChatGPTFixture(path), source_context: sourceExpired ? "expired-fixture" : "live-fixture", source_expired: sourceExpired } });
      if (path === "/api/sms/status") return route.fulfill({ json: { control_token: "controlled-entry", enabled: false, configured: false, demo_enabled: false } });
      if (path === "/api/chatgpt/status") return sourceExpired ? route.fulfill({ status: 409, json: { error: { code: "simulation_context_expired" } } }) : route.fulfill({ json: status });
      if (path === "/api/chatgpt/models") return route.fulfill({ json: { models: [{ slug: "returned-model", display_name: "Returned account model" }], selected_model: status.writer.selected_model } });
      if (path === "/api/chatgpt/connect") {
        status = { ...disconnected(), state: "connecting", attempt: { id: "attempt", state: "connecting", expires_at: "2099-01-01T00:00:00Z" } };
        return route.fulfill({ json: { attempt_id: "attempt", authorization_url: "https://auth.openai.com/oauth/authorize?state=controlled", expires_at: "2099-01-01T00:00:00Z" } });
      }
      if (path === "/api/chatgpt/cancel") { status = disconnected(); return route.fulfill({ json: {} }); }
      if (path === "/api/chatgpt/model") { assert.equal(request.postDataJSON().model, "returned-model"); status.writer.selected_model = "returned-model"; return route.fulfill({ json: {} }); }
      if (path === "/api/chatgpt/resume") { status.writer.usage_paused = false; return route.fulfill({ json: {} }); }
      if (path === "/api/tara/conversation") return route.fulfill({ json: { schema_version: "terra-conversation-v1", setup: { stage: "crop", crops: [] }, orchard_id: null, orchards: [], turns: [], pending_requests: [], initial_prompt: "What are you growing?", monitoring: { status: "not_configured" }, notifications: { enabled: false, capability: "browser_open" } } });
      if (path === "/api/orchards") return route.fulfill({ json: { orchards: [] } });
      if (path === "/api/context") return route.fulfill({ json: { schema_version: "terra-live-context-v1", source: "CIMIS", configured: false, station_presets: [] } });
      return route.fulfill({ status: 404, json: { error: { code: "not_found" } } });
    });
    await page.goto(url.href);
    const signIn = page.getByRole("button", { name: "Sign in with ChatGPT", exact: true });
    await signIn.waitFor();
    assert.equal(await page.locator(".app-window").count(), 0);
    assert.equal(await page.getByRole("button").count(), 1);
    assert.deepEqual(writes, [], "rendering does not connect or infer");
    await signIn.click();
    const cancel = page.getByRole("button", { name: "Cancel sign-in", exact: true });
    await cancel.waitFor();
    assert.deepEqual(writes, [{ path: "/api/chatgpt/connect", method: "POST", body: {} }]);
    assert.deepEqual(await page.evaluate(() => window.__authLinks), ["https://auth.openai.com/oauth/authorize?state=controlled"]);
    await cancel.click();
    await signIn.waitFor();
    assert.equal(writes.at(-1).path, "/api/chatgpt/cancel");
    assert.equal(await signIn.evaluate((element) => element === document.activeElement), true);

    status = eligibleChatGPTFixture("/api/chatgpt/status"); status.writer.selected_model = null;
    await page.reload();
    const model = page.getByLabel("ChatGPT model", { exact: true });
    await model.waitFor();
    assert.equal(await model.inputValue(), "");
    assert.equal(writes.filter((item) => item.path === "/api/chatgpt/model").length, 0);
    await model.selectOption("returned-model");
    const composer = page.getByRole("textbox", { name: "Message Terra", exact: true });
    await composer.waitFor();
    assert.equal(writes.filter((item) => item.path === "/api/chatgpt/model").length, 1);
    await page.getByRole("button", { name: "Message writing", exact: true }).click();
    assert.equal(await page.getByRole("dialog", { name: "Message writing", exact: true }).count(), 1);
    await page.getByRole("button", { name: "Close message writing", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "Message writing", exact: true }).evaluate((element) => element === document.activeElement), true);
    const before = writes.length;
    await page.reload(); await composer.waitFor();
    assert.equal(writes.length, before, "restoring a selected account does not sign in or infer again");
    status.writer.usage_paused = true;
    await page.reload();
    await page.getByRole("link", { name: "Manage ChatGPT usage", exact: true }).waitFor();
    assert.equal(await page.locator(".app-window").count(), 0);
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await composer.waitFor();
    assert.equal(writes.at(-1).path, "/api/chatgpt/resume");
    status.writer.selected_model = "removed-saved-model";
    const beforeInvalidation = writes.length;
    await page.reload();
    await page.getByLabel("ChatGPT model", { exact: true }).waitFor();
    assert.equal(await page.locator(".app-window").count(), 0);
    assert.equal(writes.length, beforeInvalidation, "catalog invalidation never infers or selects a default");
    sourceExpired = true;
    await page.reload();
    await page.getByRole("button", { name: "Return to Terra", exact: true }).click();
    await signIn.waitFor();
    assert.equal(sourceExpired, false);
    assert.equal(writes.at(-1).path, "/api/simulation/connection");
    assert.equal(writes.at(-1).method, "DELETE");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
