import assert from "node:assert/strict";
import test from "node:test";
import { authorizationUrl, connectionError, connectionPhase, connectionPollDelay, connectionStatus, modelCatalog } from "../src/chatgpt-state.js";

const NOW = Date.parse("2026-10-24T12:00:00Z");
const status = (change = {}) => ({ schema_version: "terra-chatgpt-connection-v1", state: "disconnected", plan_enabled: false, active_account_id: null, accounts: [], writer: { connected: false, selected_model: null, usage_paused: false }, ...change });

test("provider permission errors preserve recovery instead of blaming the local session", () => {
  const error = connectionError(403, { error: { code: "subscription_sharing_user_not_eligible", message: "This account is not eligible." } });
  assert.equal(error.localSession, false);
  assert.equal(error.message, "This account is not eligible.");
  assert.equal(connectionError(403, { error: { code: "http_error" } }).localSession, true);
  assert.equal(connectionError(429, { error: { code: "subscription_sharing_usage_limit_exceeded", message: "Review app usage." } }).message, "Review app usage.");
});

test("connection links accept only the exact HTTPS authorization origin", () => {
  assert.equal(authorizationUrl("https://auth.openai.com/oauth/authorize?state=test"), "https://auth.openai.com/oauth/authorize?state=test");
  for (const value of ["http://auth.openai.com/oauth", "https://auth.openai.com.evil.test/oauth", "https://auth.openai.com:8443/oauth", "https://person:secret@auth.openai.com/oauth", "javascript:alert(1)", "/oauth"]) assert.throws(() => authorizationUrl(value));
});

test("a real account model catalog never creates or chooses a default model", () => {
  assert.deepEqual(modelCatalog({ models: [], selected_model: "invented" }), { models: [], selected_model: "" });
  const models = [{ slug: "catalog-model", display_name: "Account model" }];
  assert.equal(modelCatalog({ models, selected_model: null }).selected_model, "");
  assert.equal(modelCatalog({ models, selected_model: "catalog-model" }).selected_model, "catalog-model");
  assert.throws(() => modelCatalog({ models: [...models, ...models] }));
  assert.throws(() => modelCatalog({ models: [{ slug: "catalog-model" }] }));
});

test("connection state distinguishes plan consent, reconnect and usage pause", () => {
  assert.equal(connectionPhase(null, NOW), "unavailable");
  for (const state of ["disconnected", "connected", "plan_disabled", "reauth_required", "error"]) assert.equal(connectionPhase(status({ state }), NOW), state);
  assert.equal(connectionPhase(status({ state: "connected", plan_enabled: true, active_account_id: "opaque", writer: { usage_paused: true } }), NOW), "paused");
  assert.equal(connectionPhase(status({ state: "plan_disabled", writer: { usage_paused: true } }), NOW), "plan_disabled");
  assert.equal(connectionPhase(status({ state: "reauth_required", plan_enabled: true, active_account_id: "opaque", writer: { usage_paused: true } }), NOW), "reauth_required");
});

test("connecting polls stop at the actual expiry or bounded fallback deadline", () => {
  const connecting = status({ state: "connecting", attempt: { id: "attempt", expires_at: new Date(NOW + 4500).toISOString() } });
  assert.equal(connectionPhase(connecting, NOW), "connecting");
  assert.equal(connectionPollDelay(connecting, NOW, NOW + 120000), 2000);
  assert.equal(connectionPollDelay(connecting, NOW + 4000, NOW + 120000), 500);
  assert.equal(connectionPollDelay(connecting, NOW + 4500, NOW + 120000), 0);
  assert.equal(connectionPhase(connecting, NOW + 4500), "expired");
  assert.equal(connectionPollDelay(status({ state: "connecting" }), NOW, NOW + 120000), 2000);
  assert.equal(connectionPollDelay(status({ state: "connecting" }), NOW + 120000, NOW + 120000), 0);
  assert.equal(connectionPollDelay(status(), NOW, NOW + 120000), null);
});

test("invalid status cannot appear as a connected or empty account state", () => {
  assert.equal(connectionStatus(status()).state, "disconnected");
  assert.throws(() => connectionStatus({ state: "connected" }));
  assert.throws(() => connectionStatus(status({ state: "unexpected" })));
  assert.throws(() => connectionStatus(status({ accounts: [{ id: "one", label: "Account" }, { id: "one", label: "Duplicate" }] })));
});
