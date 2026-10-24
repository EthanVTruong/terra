import assert from "node:assert/strict";
import test from "node:test";
import { createTaraOperation, mergeTaraTimeline, reconcilableTaraRequests, restoreFarmLocation, restoreTaraOperation, taraError, validateFarmLocation, validateTaraResult, validateTaraSnapshot } from "../src/tara-state.js";

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
  assert.equal(taraError({ error: { code: "request_already_completed" } }, 409).alreadyCompleted, true);
  assert.equal(restoreTaraOperation({ ...operation, status: "result_available" }).status, "result_available");
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

test("field notes and conversation remain chronological, with stable saved-event ordering and no duplicate answer", () => {
  const turns = [turn({ id: "saved-location", role: "event", kind: "location" }), turn({ id: "next-question", role: "assistant", text: "Want browser alerts?" }), turn({ id: "answer-turn", role: "assistant", created_at: "2026-10-24T16:02:00Z", data: { message_id: "stored-answer" } })];
  const notes = [{ id: "alert-later", kind: "proactive", created_at: "2026-10-24T16:03:00Z" }, { id: "stored-answer", kind: "answer", created_at: "2026-10-24T16:02:00Z" }, { id: "alert-first", kind: "proactive", created_at: "2026-10-24T15:59:00Z" }];
  assert.deepEqual(mergeTaraTimeline(turns, notes).map((entry) => entry.value.id), ["alert-first", "saved-location", "next-question", "answer-turn", "alert-later"]);
  assert.equal(mergeTaraTimeline(turns, [...notes, notes[0]]).filter((entry) => entry.value.id === "alert-later").length, 1);
  assert.equal(mergeTaraTimeline(turns, [{ id: "bad", created_at: "unknown" }]).length, turns.length);
});

test("recovery is available only for a demonstrably exited owner with explicit server permission", () => {
  const pending = { request_id: "old-request", created_at: "2026-10-24T16:00:00Z" };
  assert.equal(reconcilableTaraRequests(snapshot({ pending_requests: [pending] })).length, 0);
  for (const owner_status of ["active", "unknown"]) assert.equal(reconcilableTaraRequests(snapshot({ pending_requests: [{ ...pending, owner_status, reconcilable: true }] })).length, 0);
  assert.equal(reconcilableTaraRequests(snapshot({ pending_requests: [{ ...pending, owner_status: "exited", reconcilable: false }] })).length, 0);
  assert.equal(reconcilableTaraRequests(snapshot({ pending_requests: [{ ...pending, owner_status: "exited", reconcilable: true }] })).length, 1);
  assert.throws(() => validateTaraSnapshot(snapshot({ pending_requests: [{ ...pending, owner_status: "maybe", reconcilable: true }] })), /unfinished request/);
});

test("a recovery acknowledgment has a new durable identity and requires the matching saved uncertainty notice", () => {
  const operation = createTaraOperation("reconcile", { target_request_id: "old-request", confirm_uncertain: true, orchard_id: "farm-1" }, "ack-request");
  const restored = restoreTaraOperation(JSON.parse(JSON.stringify(operation)));
  assert.equal(restored.path, "/api/tara/reconcile");
  assert.equal(restored.body.request_id, "ack-request");
  assert.equal(restored.body.target_request_id, "old-request");
  assert.equal(restoreTaraOperation({ ...operation, body: { ...operation.body, confirm_uncertain: false } }), null);
  assert.equal(restoreTaraOperation({ ...operation, path: "/api/tara/messages" }), null);
  const notice = turn({ id: "ack:notice", role: "event", kind: "notice", text: "Earlier request may have used ChatGPT allowance. Continuing will not retry it.", data: { type: "usage_uncertain", target_request_id: "old-request" } });
  const result = { request_id: "ack-request", outcome: "none", user_turn: null, assistant_turn: null, reaction: null, event: notice, state: snapshot({ turns: [notice] }) };
  assert.equal(validateTaraResult(result, operation).event.id, notice.id);
  assert.throws(() => validateTaraResult({ ...result, state: snapshot({ turns: [] }) }, operation), /reconciled/);
  assert.throws(() => validateTaraResult({ ...result, state: snapshot({ turns: [notice], orchard_id: "other-farm" }) }, operation), /different farm/);
  assert.throws(() => validateTaraResult({ ...result, state: snapshot({ turns: [notice], pending_requests: [{ request_id: "old-request", created_at: notice.created_at, owner_status: "exited", reconcilable: true }] }) }, operation), /reconciled/);
  assert.throws(() => validateTaraResult({ ...result, event: { ...notice, data: { ...notice.data, target_request_id: "other-request" } } }, operation), /reconciled/);
});
