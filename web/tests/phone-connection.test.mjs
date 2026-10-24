import assert from "node:assert/strict";
import test from "node:test";
import { phoneConnectionState } from "../src/phoneConnection.js";

const ready = { configured: true, enabled: true, suppressed: false, recipient: "***0126", configuration_errors: [] };

test("connection attempts distinguish an unknown phone from unfinished setup", () => {
  assert.equal(phoneConnectionState(null).kind, "checking");
  assert.equal(phoneConnectionState({ configured: false }).kind, "disconnected");
  assert.equal(phoneConnectionState({ configured: false }, { attempted: true }).kind, "setup");
});

test("configured credentials alone never claim a ready connection", () => {
  assert.equal(phoneConnectionState({ ...ready, enabled: false }).kind, "setup");
  assert.equal(phoneConnectionState({ ...ready, recipient: null }).kind, "setup");
  assert.equal(phoneConnectionState({ ...ready, configuration_errors: ["delivery_store_unavailable"] }).kind, "setup");
  assert.equal(phoneConnectionState(ready).kind, "connected");
});

test("checking and network failures take precedence over cached readiness", () => {
  assert.equal(phoneConnectionState(ready, { checking: true }).kind, "checking");
  assert.equal(phoneConnectionState(ready, { unavailable: true }).kind, "unavailable");
});

test("pausing delivery retains the configured phone", () => {
  assert.equal(phoneConnectionState({ ...ready, enabled: false, suppressed: true }).kind, "paused");
});

test("connection readiness remains separate from demo and orchard preferences", () => {
  assert.equal(phoneConnectionState({ ...ready, demo_enabled: false, subscribed: false }).kind, "connected");
  assert.equal(phoneConnectionState({ ...ready, demo_enabled: true, subscribed: true }).kind, "connected");
});
