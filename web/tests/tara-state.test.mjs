import assert from "node:assert/strict";
import test from "node:test";
import { bindTaraOperationSource, createTaraOperation, deviceLocationFromPosition, mergeTaraTimeline, reconcilableTaraRequests, restoreTaraOperation, taraDisplayText, taraError, taraLocationSetupKey, taraNeedsNotificationChoice, taraNotificationEnabledAt, validateTaraResult, validateTaraSnapshot } from "../src/tara-state.js";

const turn = (extra = {}) => ({ id: "turn-1", role: "user", kind: "text", text: "Thanks", created_at: "2026-10-24T16:00:00Z", reply_to: null, reactions: [], delivery: "local_saved", ...extra });
const snapshot = (extra = {}) => ({ schema_version: "terra-conversation-v1", setup: { stage: "complete", crops: ["almond"], orchard_id: "farm-1" }, orchard_id: "farm-1", orchards: [{ id: "farm-1", name: "West Orchard" }], turns: [], monitoring: { status: "pending" }, notifications: { enabled: false, capability: "browser_open" }, pending_requests: [], ...extra });

test("only exact saved app-owned location prompts receive current display guidance, preserving their stored text", () => {
  for (const text of ["Where's your farm? Use your location if you're there, or enter coordinates.", "Confirm your farm location to start monitoring."]) {
    const saved = Object.freeze(turn({ role: "assistant", text, data: Object.freeze({ action: "location_choice" }) }));
    assert.equal(taraDisplayText(saved), "Where are you growing? Share your location.");
    assert.equal(saved.text, text);
    for (const other of [{ ...saved, role: "user" }, { ...saved, role: "event" }, { ...saved, data: {} }, { ...saved, data: { action: "answer" } }, { ...saved, text: `${text} ` }, { ...saved, text: "Weather coverage is spotty since Jan 14." }]) assert.equal(taraDisplayText(other), other.text);
  }
});

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

test("a persisted operation keeps its original source even when reloaded before the rejected reply", () => {
  const live = { context: "live-A", kind: "live" }, replay = { context: "sim-B", kind: "simulation" };
  const operations = [
    createTaraOperation("message", { text: "How much chill?", orchard_id: "farm-1" }, "source-message"),
    createTaraOperation("setup", { action: "device_location", device_location: { lat: 38.6, lon: -121.84, accuracy_m: 8 } }, "source-location"),
    createTaraOperation("reconcile", { target_request_id: "prior-request", confirm_uncertain: true, orchard_id: "farm-1" }, "source-recovery"),
  ];
  for (const operation of operations) {
    const saved = JSON.stringify(bindTaraOperationSource(operation, live));
    const restored = restoreTaraOperation(JSON.parse(saved));
    assert.equal(restored.sourceContext, "live-A");
    assert.deepEqual(bindTaraOperationSource(restored, live).body, operation.body);
    for (const changed of [replay, { context: "live-after-restart", kind: "live" }]) {
      assert.throws(() => bindTaraOperationSource(restored, changed), (error) => error.rejected === true && error.sourceChanged === true);
      assert.equal(restored.sourceContext, "live-A");
    }
  }
});

test("legacy pending operations can resume only in confirmed live or standalone state", () => {
  const operation = createTaraOperation("message", { text: "My saved draft", orchard_id: "farm-1" }, "legacy-request");
  const restored = restoreTaraOperation(JSON.parse(JSON.stringify(operation)));
  for (const source of [{ context: "live-A", kind: "live" }, { context: "standalone-replay", kind: "isolated" }]) {
    assert.equal(bindTaraOperationSource(restored, source).sourceContext, source.context);
  }
  for (const source of [{ context: "sim-B", kind: "simulation" }, { context: "expired", kind: "expired" }, { kind: "unconfirmed" }]) {
    assert.throws(() => bindTaraOperationSource(restored, source), (error) => error.rejected === true && error.sourceChanged === true);
  }
  // A new explicit user operation may start in a confirmed simulator.
  assert.equal(bindTaraOperationSource(operation, { context: "sim-B", kind: "simulation" }).sourceContext, "sim-B");
  assert.equal(restoreTaraOperation({ ...operation, sourceContext: null }), null);
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

test("native location submits only coordinates and usable accuracy, without reading GPS altitude", () => {
  const coords = { latitude: 38.6, longitude: -121.84, accuracy: 8 };
  Object.defineProperty(coords, "altitude", { get() { throw new Error("GPS altitude must not be used"); } });
  assert.deepEqual(deviceLocationFromPosition({ coords }), { lat: 38.6, lon: -121.84, accuracy_m: 8 });
  for (const accuracy of [undefined, null, NaN, Infinity, -1]) assert.equal(deviceLocationFromPosition({ coords: { ...coords, accuracy } }).accuracy_m, null);
  assert.equal(deviceLocationFromPosition({ coords: { latitude: 0, longitude: 0, accuracy: 0 } }).accuracy_m, 0);
  for (const value of [null, {}, { coords: {} }, { coords: { latitude: 91, longitude: 0 } }, { coords: { latitude: "38.6", longitude: 0 } }]) assert.throws(() => deviceLocationFromPosition(value));
});

test("a device-location retry retains its original identity and rejects manual or malformed fields", () => {
  const operation = createTaraOperation("setup", { action: "device_location", device_location: { lat: 38.6, lon: -121.84, accuracy_m: null } }, "location-request");
  assert.deepEqual(restoreTaraOperation(JSON.parse(JSON.stringify(operation))).body, operation.body);
  for (const patch of [{ lat: 91 }, { lon: "-121" }, { accuracy_m: -1 }, { name: "Made-up farm" }, { altitude: 70 }]) assert.equal(restoreTaraOperation({ ...operation, body: { ...operation.body, device_location: { ...operation.body.device_location, ...patch } } }), null);
  assert.equal(taraError({ error: { code: "location_lookup_failed" } }, 503).message, "Couldn’t get your location details. Try again.");
  assert.equal(taraError({ error: { code: "setup_changed" } }, 409).setupChanged, true);
});

test("a device location clears its pending operation only for the actual saved location event", () => {
  const operation = createTaraOperation("setup", { action: "device_location", device_location: { lat: 38.6, lon: -121.84, accuracy_m: null } }, "location-result");
  const event = turn({ id: "location-saved", role: "event", kind: "location", text: "Location shared", data: { source: "device", saved: true, confirmed: true, orchard_id: "farm-1" } });
  const result = { request_id: operation.body.request_id, outcome: "none", user_turn: null, assistant_turn: null, reaction: null, event, state: snapshot({ turns: [event] }) };
  assert.equal(validateTaraResult(result, operation).event.id, event.id);
  for (const patch of [{ event: null }, { event: { ...event, data: { ...event.data, source: "manual" } } }, { state: snapshot({ turns: [] }) }, { state: snapshot({ turns: [{ ...event, data: { ...event.data, saved: false } }] }) }, { state: snapshot({ orchard_id: "other-farm", turns: [event] }) }]) assert.throws(() => validateTaraResult({ ...result, ...patch }, operation));
});

test("late native results can be matched to the same setup and location prompt only", () => {
  const setup = snapshot({ orchard_id: null, setup: { stage: "location", crops: ["almond"], crop_description: "Almonds" }, turns: [turn({ id: "location-question", role: "assistant", data: { action: "location_choice" } })] });
  const key = taraLocationSetupKey(setup);
  assert.equal(taraLocationSetupKey(structuredClone(setup)), key);
  assert.notEqual(taraLocationSetupKey({ ...setup, setup: { ...setup.setup, crops: ["peach"] } }), key);
  assert.notEqual(taraLocationSetupKey({ ...setup, turns: [turn({ id: "new-location-question", role: "assistant", data: { action: "location_choice" } })] }), key);
  assert.equal(taraLocationSetupKey({ ...setup, setup: { ...setup.setup, stage: "complete" } }), null);
});

test("field notes and conversation remain chronological, with stable saved-event ordering and no duplicate answer", () => {
  const turns = [turn({ id: "saved-location", role: "event", kind: "location" }), turn({ id: "next-question", role: "assistant", text: "Want browser alerts?" }), turn({ id: "answer-turn", role: "assistant", created_at: "2026-10-24T16:02:00Z", data: { message_id: "stored-answer" } })];
  const notes = [{ id: "alert-later", kind: "proactive", created_at: "2026-10-24T16:03:00Z" }, { id: "stored-answer", kind: "answer", created_at: "2026-10-24T16:02:00Z" }, { id: "alert-first", kind: "proactive", created_at: "2026-10-24T15:59:00Z" }];
  assert.deepEqual(mergeTaraTimeline(turns, notes).map((entry) => entry.value.id), ["alert-first", "saved-location", "next-question", "answer-turn", "alert-later"]);
  assert.equal(mergeTaraTimeline(turns, [...notes, notes[0]]).filter((entry) => entry.value.id === "alert-later").length, 1);
  assert.equal(mergeTaraTimeline(turns, [{ id: "bad", created_at: "unknown" }]).length, turns.length);
});

test("real publication times interleave replay notes and turns without changing their shared observation clock", () => {
  const sourceClock = "2026-03-11T11:00:00Z";
  const turns = [
    turn({ id: "farm-saved", role: "event", kind: "location", created_at: sourceClock, published_at: "2026-10-24T18:00:00Z" }),
    turn({ id: "question", text: "Is the weather data usable?", created_at: sourceClock, published_at: "2026-10-24T18:00:02+00:00" }),
    turn({ id: "answer", role: "assistant", text: "Weather data is spotty.", created_at: sourceClock, published_at: "2026-10-24T18:00:03Z", data: { message_id: "linked-answer" } }),
    turn({ id: "thanks", text: "Thanks", created_at: sourceClock, published_at: "2026-10-24T18:00:03Z" }),
  ];
  const notes = [
    { id: "linked-answer", kind: "answer", created_at: sourceClock, published_at: "2026-10-24T18:00:03Z" },
    { id: "coverage-limited", text: "Weather data is spotty.", created_at: sourceClock, published_at: "2026-10-24T18:00:01Z" },
    { id: "welcome", text: "Weather support is usable.", created_at: sourceClock, published_at: "2026-10-24T18:00:00.500Z" },
  ];
  const originals = structuredClone({ turns, notes });
  for (const value of [...turns, ...notes]) Object.freeze(value);
  Object.freeze(turns); Object.freeze(notes);
  const merged = mergeTaraTimeline(turns, notes);
  assert.deepEqual(merged.map(({ value }) => value.id), ["farm-saved", "welcome", "coverage-limited", "question", "answer", "thanks"]);
  assert.ok(merged.every(({ value }) => value.created_at === sourceClock));
  assert.deepEqual({ turns, notes }, originals);
});

test("legacy native notes sharing a replay clock display oldest first instead of latest coverage above welcome", () => {
  const created_at = "2026-03-11T11:00:00Z";
  const newestFirst = [
    { id: "coverage-limited", text: "Weather data is spotty.", created_at },
    { id: "welcome", text: "Weather support is usable.", created_at },
  ];
  assert.deepEqual(mergeTaraTimeline([], newestFirst).map(({ value }) => value.id), ["welcome", "coverage-limited"]);
  assert.deepEqual(newestFirst.map(({ id }) => id), ["coverage-limited", "welcome"]);
});

test("malformed optional publication clocks fall back to created_at without losing notes or stable turn ties", () => {
  const created_at = "2026-03-11T11:00:00Z";
  const turns = [turn({ id: "first", created_at, published_at: null }), turn({ id: "second", created_at, published_at: "not-a-time" })];
  const notes = [
    { id: "later", created_at: "2026-03-11T12:00:00Z", published_at: "2026-10-24" },
    { id: "earlier", created_at, published_at: 42 },
  ];
  assert.deepEqual(mergeTaraTimeline(turns, notes).map(({ value }) => value.id), ["first", "second", "earlier", "later"]);
  assert.deepEqual(mergeTaraTimeline([], [...notes, notes[1]]).map(({ value }) => value.id), ["earlier", "later"]);
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

test("browser alerts require the latest saved server notification choice, not a client clock or preference alone", () => {
  const enabled = snapshot({ notifications: { enabled: true, capability: "browser_open" } });
  const event = turn({ id: "enable", role: "event", kind: "notification", created_at: "2026-10-24T16:01:00Z", data: { saved: true, enabled: true } });
  assert.equal(taraNotificationEnabledAt(enabled), null);
  assert.equal(taraNotificationEnabledAt({ ...enabled, turns: [{ ...event, role: "assistant" }] }), null);
  assert.equal(taraNotificationEnabledAt({ ...enabled, turns: [{ ...event, data: { enabled: true } }] }), null);
  assert.equal(taraNotificationEnabledAt({ ...enabled, turns: [event] }), Date.parse(event.created_at));
  const disabled = { ...event, id: "disable", created_at: "2026-10-24T16:02:00Z", data: { saved: true, enabled: false } };
  assert.equal(taraNotificationEnabledAt({ ...enabled, turns: [event, disabled] }), null);
  const enabledAgain = { ...event, id: "enable-again", created_at: "2026-10-24T16:03:00Z" };
  assert.equal(taraNotificationEnabledAt({ ...enabled, turns: [event, disabled, enabledAgain] }), Date.parse(enabledAgain.created_at));
  assert.equal(taraNotificationEnabledAt({ ...enabled, notifications: { enabled: false, capability: "browser_open" }, turns: [event] }), null);
});

test("an existing farm exposes the requested consent choice until a choice is actually saved or canceled", () => {
  const request = turn({ id: "request-notifications", role: "assistant", data: { action: "notification_choice" } });
  const saved = turn({ id: "saved-notifications", role: "event", kind: "notification", data: { saved: true, enabled: false } });
  assert.equal(taraNeedsNotificationChoice(snapshot()), false);
  assert.equal(taraNeedsNotificationChoice(snapshot({ turns: [saved, request] })), true);
  assert.equal(taraNeedsNotificationChoice(snapshot({ turns: [saved, request, turn({ id: "thanks" })] })), true);
  assert.equal(taraNeedsNotificationChoice(snapshot({ turns: [request, saved] })), false);
  assert.equal(taraNeedsNotificationChoice(snapshot({ turns: [request, turn({ id: "off", role: "assistant", data: { action: "notifications_off" } })] })), false);
  assert.equal(taraNeedsNotificationChoice(snapshot({ setup: { stage: "notifications", crops: ["almond"] } })), true);
  assert.equal(taraNeedsNotificationChoice(snapshot({ setup: { stage: "crop", crops: [] }, turns: [request], orchard_id: null })), false);
});
