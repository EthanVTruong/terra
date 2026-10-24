const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const identifier = (value) => typeof value === "string" && value.length > 0 && value.length <= 160;
const nullableId = (value) => value === null || identifier(value);
const date = (value) => typeof value === "string" && Number.isFinite(Date.parse(value));
const stages = new Set(["crop", "location", "notifications", "complete"]);
const outcomes = new Set(["message", "reaction", "none"]);
const monitoringStatuses = new Set(["not_configured", "pending", "ready", "unavailable"]);

function validateReaction(value, target) {
  if (!object(value) || value.emoji !== "👍" || !identifier(value.target_id) || (target && value.target_id !== target)) throw new Error("A conversation reaction could not be read. Your previous messages are retained.");
  return value;
}

export function validateTaraTurn(value) {
  if (!object(value) || !identifier(value.id) || !["user", "assistant", "event"].includes(value.role) || !["text", "location", "notification"].includes(value.kind) || typeof value.text !== "string" || value.text.length > 12000 || !date(value.created_at) || !nullableId(value.reply_to) || !Array.isArray(value.reactions) || value.delivery !== "local_saved") throw new Error("A conversation message could not be read. Your previous messages are retained.");
  if (value.data !== undefined && value.data !== null && !object(value.data)) throw new Error("A conversation event could not be read.");
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
  return value;
}

export function validateTaraResult(value, operation) {
  if (!object(value) || value.request_id !== operation.body.request_id || !outcomes.has(value.outcome)) throw new Error("The response could not be confirmed. Retry the same request.");
  const state = validateTaraSnapshot(value.state);
  if (operation.kind === "message" && operation.body.orchard_id && state.orchard_id !== operation.body.orchard_id) throw new Error("The response returned a different farm. Your previous messages are retained.");
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
  return value;
}

export function createTaraOperation(kind, body, requestId) {
  if (!["message", "setup"].includes(kind) || !identifier(requestId)) throw new Error("The request could not be prepared.");
  return { version: 1, kind, path: kind === "message" ? "/api/tara/messages" : "/api/tara/setup", body: { ...body, request_id: requestId }, status: "sending" };
}

export function restoreTaraOperation(value) {
  if (!object(value) || value.version !== 1 || !["message", "setup"].includes(value.kind) || value.path !== (value.kind === "message" ? "/api/tara/messages" : "/api/tara/setup") || !object(value.body) || !identifier(value.body.request_id)) return null;
  if (value.kind === "message" && (typeof value.body.text !== "string" || !value.body.text.trim() || value.body.text.length > 1000)) return null;
  if (value.kind === "setup" && !["location", "notifications", "restart"].includes(value.body.action)) return null;
  return { ...value, status: value.status === "in_progress" ? "in_progress" : "unconfirmed" };
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

export function restoreFarmLocation(value) {
  if (!object(value) || !["name", "lat", "lon", "elevation"].every((key) => typeof value[key] === "string") || typeof value.confirmed !== "boolean") return { name: "", lat: "", lon: "", elevation: "", source: "manual", confirmed: false };
  return { name: value.name.slice(0, 80), lat: value.lat.slice(0, 40), lon: value.lon.slice(0, 40), elevation: value.elevation.slice(0, 40), source: ["preset", "device"].includes(value.source) ? value.source : "manual", confirmed: value.confirmed };
}

export function validateFarmLocation(draft) {
  const lat = Number(draft.lat), lon = Number(draft.lon), elevation = Number(draft.elevation);
  if (!draft.name.trim() || draft.name.trim().length > 80) throw new Error("Give this farm a short name.");
  if (![draft.lat, draft.lon, draft.elevation].every((value) => String(value).trim()) || !Number.isFinite(lat) || Math.abs(lat) > 90 || !Number.isFinite(lon) || Math.abs(lon) > 180 || !Number.isFinite(elevation) || elevation < -1500 || elevation > 20000) throw new Error("Enter valid latitude, longitude and elevation in feet.");
  if (!draft.confirmed) throw new Error("Confirm that the location and elevation describe your farm.");
  return { name: draft.name.trim(), lat, lon, elevation_ft: elevation, source: ["device", "preset"].includes(draft.source) ? draft.source : "manual", confirmed: true };
}

export function taraError(body, status) {
  const code = body?.error?.code || body?.detail?.code;
  if (status === 409 && code === "request_in_progress") return Object.assign(new Error("Terra is still handling this request. Refresh its status before sending another."), { inProgress: true });
  if (status === 403) return new Error("Reconnect to the local Terra page, then retry this request.");
  if (status === 422) return Object.assign(new Error("That request could not be saved. Check the fields and retry."), { rejected: true });
  if (status === 429 || code === "usage_limit_reached") return new Error("Message writing is paused at its usage limit. Your saved monitoring notes are still available.");
  return new Error("Terra could not confirm this request. Your draft is retained; retry the same request.");
}
