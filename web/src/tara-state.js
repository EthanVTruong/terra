import { publicationTime } from "./presentation.js";

const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const identifier = (value) => typeof value === "string" && value.length > 0 && value.length <= 160;
const nullableId = (value) => value === null || identifier(value);
const date = (value) => typeof value === "string" && Number.isFinite(Date.parse(value));
const stages = new Set(["crop", "location", "notifications", "complete"]);
const outcomes = new Set(["message", "reaction", "none"]);
const monitoringStatuses = new Set(["not_configured", "pending", "ready", "unavailable"]);
const previousLocationPrompts = new Set(["Where's your farm? Use your location if you're there, or enter coordinates.", "Confirm your farm location to start monitoring."]);

export function taraDisplayText(turn) {
  return turn.role === "assistant" && turn.data?.action === "location_choice" && previousLocationPrompts.has(turn.text) ? "Where are you growing? Share your location." : turn.text;
}

function validateReaction(value, target) {
  if (!object(value) || value.emoji !== "👍" || !identifier(value.target_id) || (target && value.target_id !== target)) throw new Error("A conversation reaction could not be read. Your previous messages are retained.");
  return value;
}

export function validateTaraTurn(value) {
  if (!object(value) || !identifier(value.id) || !["user", "assistant", "event"].includes(value.role) || !["text", "location", "notification", "notice"].includes(value.kind) || typeof value.text !== "string" || value.text.length > 12000 || !date(value.created_at) || !nullableId(value.reply_to) || !Array.isArray(value.reactions) || value.delivery !== "local_saved") throw new Error("A conversation message could not be read. Your previous messages are retained.");
  if (value.data !== undefined && value.data !== null && !object(value.data)) throw new Error("A conversation event could not be read.");
  if (value.kind === "notice" && (value.role !== "event" || value.data?.type !== "usage_uncertain" || !identifier(value.data?.target_request_id))) throw new Error("The request notice could not be read.");
  for (const reaction of value.reactions) validateReaction(reaction, value.id);
  return value;
}

export function validateTaraSnapshot(value, expectedOrchard) {
  if (!object(value) || value.schema_version !== "terra-conversation-v1" || !object(value.setup) || !stages.has(value.setup.stage) || !Array.isArray(value.setup.crops) || value.setup.crops.some((crop) => typeof crop !== "string") || !nullableId(value.orchard_id) || !Array.isArray(value.orchards) || !Array.isArray(value.turns) || !Array.isArray(value.pending_requests) || !object(value.monitoring) || !monitoringStatuses.has(value.monitoring.status) || !object(value.notifications) || !["in_app", "browser_open"].includes(value.notifications.capability) || typeof value.notifications.enabled !== "boolean") throw new Error("The conversation could not be read. Your previous messages are retained.");
  if (expectedOrchard && value.orchard_id !== expectedOrchard) throw new Error("The conversation returned a different farm. Your previous messages are retained.");
  if (value.initial_prompt !== undefined && value.initial_prompt !== null && typeof value.initial_prompt !== "string") throw new Error("The setup prompt could not be read.");
  const orchardIds = new Set();
  for (const orchard of value.orchards) {
    if (!object(orchard) || !identifier(orchard.id) || typeof orchard.name !== "string" || orchardIds.has(orchard.id)) throw new Error("The saved farms could not be read.");
    orchardIds.add(orchard.id);
  }
  const turnIds = new Set();
  for (const turn of value.turns) {
    validateTaraTurn(turn);
    if (turnIds.has(turn.id)) throw new Error("The conversation contains a repeated message. Your previous messages are retained.");
    turnIds.add(turn.id);
  }
  for (const pending of value.pending_requests) {
    if (!object(pending) || !identifier(pending.request_id) || !date(pending.created_at) || (pending.owner_status !== undefined && !["exited", "active", "unknown"].includes(pending.owner_status)) || (pending.reconcilable !== undefined && typeof pending.reconcilable !== "boolean")) throw new Error("An unfinished request could not be read. Refresh its status before continuing.");
  }
  return value;
}

export function validateTaraResult(value, operation) {
  if (!object(value) || value.request_id !== operation.body.request_id || !outcomes.has(value.outcome)) throw new Error("The response could not be confirmed. Retry the same request.");
  const state = validateTaraSnapshot(value.state);
  if (operation.kind === "message" && operation.body.orchard_id && state.orchard_id !== operation.body.orchard_id) throw new Error("The response returned a different farm. Your previous messages are retained.");
  if (operation.kind === "reconcile" && state.orchard_id !== operation.body.orchard_id) throw new Error("The request notice returned a different farm. Your previous messages are retained.");
  if (value.user_turn !== null && value.user_turn !== undefined) validateTaraTurn(value.user_turn);
  if (value.assistant_turn !== null && value.assistant_turn !== undefined) {
    validateTaraTurn(value.assistant_turn);
    if (value.assistant_turn.role !== "assistant") throw new Error("The reply could not be confirmed.");
  }
  if (value.outcome === "message" && (!value.assistant_turn || !state.turns.some((turn) => turn.id === value.assistant_turn.id && turn.role === "assistant" && turn.text === value.assistant_turn.text))) throw new Error("The saved reply could not be confirmed. Retry the same request.");
  if (value.outcome === "reaction") {
    validateReaction(value.reaction, value.user_turn?.id);
    if (!value.user_turn || value.assistant_turn) throw new Error("The reaction could not be confirmed.");
    if (!state.turns.find((turn) => turn.id === value.user_turn.id)?.reactions.some((reaction) => reaction.emoji === value.reaction.emoji && reaction.target_id === value.user_turn.id)) throw new Error("The saved reaction could not be confirmed.");
  }
  if (value.outcome === "none" && (value.assistant_turn || value.reaction)) throw new Error("A quiet response contained an unexpected reply.");
  if (operation.kind === "message" && (!value.user_turn || value.user_turn.role !== "user" || value.user_turn.text !== operation.body.text || !state.turns.some((turn) => turn.id === value.user_turn.id))) throw new Error("The sent message could not be confirmed. Retry the same request.");
  if (operation.kind === "reconcile") {
    const notice = validateTaraTurn(value.event);
    if (value.outcome !== "none" || value.user_turn || notice.kind !== "notice" || notice.data.target_request_id !== operation.body.target_request_id || !state.turns.some((turn) => turn.id === notice.id && turn.text === notice.text && turn.data?.target_request_id === operation.body.target_request_id) || state.pending_requests.some((item) => item.request_id === operation.body.target_request_id)) throw new Error("The unfinished request could not be reconciled. Refresh its status before continuing.");
  }
  if (operation.kind === "setup" && operation.body.action === "device_location") {
    const location = validateTaraTurn(value.event);
    const saved = state.turns.find((turn) => turn.id === location.id);
    if (location.role !== "event" || location.kind !== "location" || location.data?.source !== "device" || location.data?.saved !== true || location.data?.confirmed !== true || !state.orchard_id || location.data.orchard_id !== state.orchard_id || saved?.role !== "event" || saved.kind !== "location" || saved.text !== location.text || saved.data?.source !== "device" || saved.data?.saved !== true || saved.data?.confirmed !== true || saved.data?.orchard_id !== state.orchard_id) throw new Error("The shared location could not be confirmed. Retry the same location request.");
  }
  return value;
}

export function createTaraOperation(kind, body, requestId) {
  if (!["message", "setup", "reconcile"].includes(kind) || !identifier(requestId)) throw new Error("The request could not be prepared.");
  const path = kind === "message" ? "/api/tara/messages" : kind === "reconcile" ? "/api/tara/reconcile" : "/api/tara/setup";
  return { version: 1, kind, path, body: { ...body, request_id: requestId }, status: "sending" };
}

export function bindTaraOperationSource(operation, source) {
  const known = identifier(source?.context) && ["live", "simulation", "isolated"].includes(source.kind);
  const bound = Object.hasOwn(operation, "sourceContext");
  // A restored pre-feature operation came from live monitoring. It must not be
  // silently adopted by a simulator opened after that operation was saved.
  const legacyRetry = !bound && operation.status !== "sending";
  if (!known || (bound && operation.sourceContext !== source.context) ||
      (legacyRetry && !["live", "isolated"].includes(source.kind))) {
    throw Object.assign(new Error("This saved request belongs to another chat source. Your draft is retained; reload Terra and send a new request."), { rejected: true, sourceChanged: true });
  }
  return { ...operation, sourceContext: source.context };
}

export function restoreTaraOperation(value) {
  if (!object(value) || value.version !== 1 || !["message", "setup", "reconcile"].includes(value.kind) || value.path !== (value.kind === "message" ? "/api/tara/messages" : value.kind === "reconcile" ? "/api/tara/reconcile" : "/api/tara/setup") || !object(value.body) || !identifier(value.body.request_id)) return null;
  if (value.kind === "message" && (typeof value.body.text !== "string" || !value.body.text.trim() || value.body.text.length > 1000)) return null;
  if (value.kind === "setup" && !["location", "device_location", "notifications", "restart"].includes(value.body.action)) return null;
  if (value.kind === "setup" && value.body.action === "device_location" && !validDeviceLocation(value.body.device_location)) return null;
  if (value.kind === "reconcile" && (!identifier(value.body.target_request_id) || value.body.confirm_uncertain !== true || !nullableId(value.body.orchard_id))) return null;
  if (Object.hasOwn(value, "sourceContext") && !identifier(value.sourceContext)) return null;
  return { ...value, status: ["in_progress", "result_available"].includes(value.status) ? value.status : "unconfirmed" };
}

export function reconcilableTaraRequests(snapshot) {
  return (snapshot?.pending_requests || []).filter((item) => object(item) && identifier(item.request_id) && item.reconcilable === true && item.owner_status === "exited");
}

export function readTaraStorage(key, fallback = null) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

export function writeTaraStorage(key, value) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch { /* A private browser may keep this tab in memory only. */ }
}

export function readTaraDraft(key) {
  const value = readTaraStorage(key, "");
  return typeof value === "string" ? value.slice(0, 1000) : "";
}

const validDeviceLocation = (value) => object(value) && Object.keys(value).every((key) => ["lat", "lon", "accuracy_m"].includes(key)) && Number.isFinite(value.lat) && Math.abs(value.lat) <= 90 && Number.isFinite(value.lon) && Math.abs(value.lon) <= 180 && (value.accuracy_m === null || Number.isFinite(value.accuracy_m) && value.accuracy_m >= 0);

export function deviceLocationFromPosition(position) {
  const coords = position?.coords;
  const value = { lat: coords?.latitude, lon: coords?.longitude, accuracy_m: Number.isFinite(coords?.accuracy) && coords.accuracy >= 0 ? coords.accuracy : null };
  if (!validDeviceLocation(value)) throw new Error("Your location couldn’t be read. Try again.");
  return value;
}

export function taraLocationSetupKey(snapshot) {
  if (snapshot?.setup?.stage !== "location") return null;
  const prompt = [...(snapshot.turns || [])].reverse().find((turn) => turn.role === "assistant" && turn.data?.action === "location_choice");
  return JSON.stringify({ orchard_id: snapshot.orchard_id, crops: snapshot.setup.crops, variety: snapshot.setup.variety ?? null, crop_description: snapshot.setup.crop_description ?? null, prompt_id: prompt?.id ?? null });
}

export function mergeTaraTimeline(turns, fieldNotes = []) {
  const linked = new Set(turns.filter((turn) => turn.role === "assistant" && identifier(turn.data?.message_id)).map((turn) => turn.data.message_id));
  const entries = turns.map((turn, index) => ({ type: "turn", key: `turn:${turn.id}`, value: turn, order: index }));
  const noteIds = new Set();
  // Native field-note feeds are newest first, including equal source clocks.
  // Turn arrays already retain their original oldest-first publication order.
  for (const note of [...fieldNotes].reverse()) {
    if (!object(note) || !identifier(note.id) || !date(note.created_at) || linked.has(note.id) || noteIds.has(note.id)) continue;
    noteIds.add(note.id);
    entries.push({ type: "note", key: `note:${note.id}`, value: note, order: entries.length });
  }
  const timelineTime = (value) => Date.parse(publicationTime(value));
  return entries.sort((a, b) => timelineTime(a.value) - timelineTime(b.value) || a.order - b.order);
}

export function taraNotificationEnabledAt(snapshot) {
  if (snapshot?.notifications?.enabled !== true || snapshot.notifications.capability !== "browser_open") return null;
  const events = snapshot.turns || [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.role !== "event" || event.kind !== "notification" || event.data?.saved !== true) continue;
    const stamp = Date.parse(publicationTime(event));
    return event.data.enabled === true && Number.isFinite(stamp) ? stamp : null;
  }
  return null;
}

export function taraNeedsNotificationChoice(snapshot) {
  if (snapshot?.setup?.stage === "notifications") return true;
  if (snapshot?.setup?.stage !== "complete" || !snapshot.orchard_id) return false;
  const turns = snapshot.turns || [];
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn.role === "event" && turn.kind === "notification" && turn.data?.saved === true) return false;
    if (turn.role === "assistant" && turn.data?.action === "notifications_off") return false;
    if (turn.role === "assistant" && turn.data?.action === "notification_choice") return true;
  }
  return false;
}

export function taraError(body, status) {
  const code = body?.error?.code || body?.detail?.code;
  if (status === 503 && code === "location_lookup_failed") return new Error("Couldn’t get your location details. Try again.");
  if (status === 409 && code === "setup_changed") return Object.assign(new Error("Setup changed while location was loading. Refresh to continue."), { setupChanged: true });
  if (status === 409 && code === "request_already_completed") return Object.assign(new Error("The earlier reply finished. Recover its saved result without retrying generation."), { alreadyCompleted: true });
  if (status === 409 && code === "request_in_progress") return Object.assign(new Error("Terra is still handling this request. Refresh its status before sending another."), { inProgress: true });
  if (status === 403) return new Error("Reconnect to the local Terra page, then retry this request.");
  if (status === 422) return Object.assign(new Error("That request could not be saved. Check the fields and retry."), { rejected: true });
  if (status === 429 || code === "usage_limit_reached") return new Error("Message writing is paused at its usage limit. Your saved monitoring notes are still available.");
  return new Error("Terra could not confirm this request. Your draft is retained; retry the same request.");
}
