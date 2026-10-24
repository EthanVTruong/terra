import assert from "node:assert/strict";
import test from "node:test";
import { createTaraOperation, restoreFarmLocation, restoreTaraOperation, taraError, validateFarmLocation, validateTaraResult, validateTaraSnapshot } from "../src/tara-state.js";

const turn = (extra = {}) => ({ id: "turn-1", role: "user", kind: "text", text: "Thanks", created_at: "2026-10-24T16:00:00Z", reply_to: null, reactions: [], delivery: "local_saved", ...extra });
const snapshot = (extra = {}) => ({ schema_version: "terra-conversation-v1", setup: { stage: "complete", crops: ["almond"], orchard_id: "farm-1" }, orchard_id: "farm-1", orchards: [{ id: "farm-1", name: "West Orchard" }], turns: [], monitoring: { status: "pending" }, notifications: { enabled: false, capability: "browser_open" }, pending_requests: [], ...extra });

test("unknown and malformed snapshots retain the previous conversation instead of inventing state", () => {
  for (const input of [null, {}, snapshot({ schema_version: "future" }), snapshot({ turns: [turn(), turn()] }), snapshot({ turns: [turn({ delivery: "sms_delivered" })] }), snapshot({ notifications: { enabled: "yes", capability: "browser_open" } }), snapshot({ setup: { stage: "finished", crops: [] } })]) assert.throws(() => validateTaraSnapshot(input));
  assert.throws(() => validateTaraSnapshot(snapshot(), "other-farm"), /different farm/);
});

test("no-reply results preserve the actual accepted user turn and local receipt", () => {
  const operation = createTaraOperation("message", { text: "Thanks", orchard_id: "farm-1", reply_to: null }, "request-1");
  const user = turn();
  const value = { request_id: "request-1", outcome: "none", user_turn: user, assistant_turn: null, reaction: null, state: snapshot({ turns: [user] }) };
  assert.equal(validateTaraResult(value, operation).state.turns[0].delivery, "local_saved");
  assert.throws(() => validateTaraResult({ ...value, assistant_turn: turn({ id: "assistant-1", role: "assistant", text: "Delivered" }) }, operation), /unexpected reply/);
  assert.throws(() => validateTaraResult({ ...value, request_id: "wrong-request" }, operation), /not be confirmed/);
  assert.throws(() => validateTaraResult({ ...value, state: snapshot({ orchard_id: "another-farm", turns: [user] }) }, operation), /different farm/);
});

test("a reaction must attach to the same persisted user turn without an extra assistant bubble", () => {
  const operation = createTaraOperation("message", { text: "Got it", orchard_id: "farm-1" }, "request-2");
  const reaction = { emoji: "👍", target_id: "turn-1" };
  const user = turn({ text: "Got it", reactions: [reaction] });
  const value = { request_id: "request-2", outcome: "reaction", user_turn: user, assistant_turn: null, reaction, state: snapshot({ turns: [user] }) };
  assert.equal(validateTaraResult(value, operation).reaction.target_id, user.id);
  assert.throws(() => validateTaraResult({ ...value, reaction: { ...reaction, target_id: "another-turn" } }, operation));
  assert.throws(() => validateTaraResult({ ...value, state: snapshot({ turns: [turn({ text: "Got it" })] }) }, operation), /saved reaction/);
});

test("restored or retried operations preserve request identity and exact payload", () => {
  const operation = createTaraOperation("message", { text: "How much chill?", orchard_id: "farm-1", reply_to: "alert-1" }, "request-3");
  const restored = restoreTaraOperation(JSON.parse(JSON.stringify(operation)));
  assert.equal(restored.body.request_id, "request-3");
  assert.deepEqual(restored.body, operation.body);
  assert.equal(restored.status, "unconfirmed");
  assert.equal(restoreTaraOperation({ ...operation, path: "https://other-service.test" }), null);
  assert.equal(restoreTaraOperation({ ...operation, body: { ...operation.body, text: "" } }), null);
  assert.equal(restoreTaraOperation({ ...operation, body: { ...operation.body, text: "x".repeat(1001) } }), null);
  assert.equal(taraError({ error: { code: "request_in_progress" } }, 409).inProgress, true);
});

test("message outcomes require the actual persisted assistant reply", () => {
  const operation = createTaraOperation("message", { text: "How much chill?", orchard_id: "farm-1" }, "request-answer");
  const user = turn({ text: "How much chill?" });
  const assistant = turn({ id: "assistant-1", role: "assistant", text: "The estimate is unavailable." });
  const response = { request_id: "request-answer", outcome: "message", user_turn: user, assistant_turn: assistant, reaction: null, state: snapshot({ turns: [user, assistant] }) };
  assert.equal(validateTaraResult(response, operation).assistant_turn.id, assistant.id);
  assert.throws(() => validateTaraResult({ ...response, assistant_turn: null }, operation), /saved reply/);
  assert.throws(() => validateTaraResult({ ...response, state: snapshot({ turns: [user] }) }, operation), /saved reply/);
});

test("farm confirmation requires an explicit valid location and elevation, including zero", () => {
  const location = { name: "West Orchard", lat: "38.6", lon: "-121.84", elevation: "70", source: "manual", confirmed: true };
  assert.equal(validateFarmLocation(location).elevation_ft, 70);
  assert.equal(validateFarmLocation({ ...location, elevation: "0" }).elevation_ft, 0);
  for (const patch of [{ confirmed: false }, { lat: "" }, { lon: "" }, { elevation: "" }, { lat: "91" }, { elevation: "NaN" }, { elevation: "20001" }, { name: "" }]) assert.throws(() => validateFarmLocation({ ...location, ...patch }));
  assert.equal(restoreFarmLocation({ name: [] }).name, "");
  assert.equal(restoreFarmLocation(location).confirmed, true);
});
