const STATES = new Set(["disconnected", "connecting", "connected", "plan_disabled", "reauth_required", "error"]);
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const nonempty = (value) => typeof value === "string" && value.trim().length > 0;
const optionalString = (value) => value === null || value === undefined || typeof value === "string";

export const CHATGPT_USAGE_URL = "https://chatgpt.com/settings/usage";

export function connectionError(status, value) {
  const code = value?.error?.code;
  const localSession = [401, 403].includes(status) && (!code || code === "http_error" || code === "sms_unauthorized");
  const message = localSession ? "Reload message writing to reconnect to Terra."
    : typeof value?.error?.message === "string" ? value.error.message : "Message writing couldn’t be updated. Try again.";
  const error = new Error(message);
  error.code = code;
  error.localSession = localSession;
  return error;
}

export function connectionStatus(value) {
  if (!object(value) || value.schema_version !== "terra-chatgpt-connection-v1" || !STATES.has(value.state) || typeof value.plan_enabled !== "boolean" || !optionalString(value.active_account_id) || !Array.isArray(value.accounts) || !object(value.writer)) {
    throw new Error("Message writing is unavailable. Template notes still work.");
  }
  const ids = new Set();
  for (const account of value.accounts) {
    if (!object(account) || !nonempty(account.id) || !nonempty(account.label) || ids.has(account.id)) throw new Error("The ChatGPT accounts could not be loaded. Try again.");
    ids.add(account.id);
  }
  if (!optionalString(value.writer.selected_model) || typeof value.writer.usage_paused !== "boolean") throw new Error("Message writing is unavailable. Template notes still work.");
  return value;
}

export function modelCatalog(value) {
  if (!object(value) || !Array.isArray(value.models)) throw new Error("Models could not be loaded. Try again.");
  const slugs = new Set();
  for (const model of value.models) {
    if (!object(model) || !nonempty(model.slug) || !nonempty(model.display_name) || slugs.has(model.slug)) throw new Error("Models could not be loaded. Try again.");
    slugs.add(model.slug);
  }
  return { models: value.models, selected_model: slugs.has(value.selected_model) ? value.selected_model : "" };
}

export function authorizationUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error("The connection link could not be opened. Try again."); }
  if (url.origin !== "https://auth.openai.com" || url.username || url.password) throw new Error("The connection link could not be opened. Try again.");
  return url.href;
}

export function connectionPhase(status, now = Date.now()) {
  if (!status) return "unavailable";
  if (status.state === "connecting") {
    const expires = Date.parse(status.attempt?.expires_at);
    return status.attempt?.state === "expired" || (Number.isFinite(expires) && expires <= now) ? "expired" : "connecting";
  }
  if (["connected", "error"].includes(status.state) && status.writer?.usage_paused && status.active_account_id && status.plan_enabled) return "paused";
  return status.state;
}

export function connectionPollDelay(status, now, fallbackDeadline) {
  if (status?.state !== "connecting") return null;
  const expires = Date.parse(status.attempt?.expires_at);
  const deadline = Number.isFinite(expires) ? expires : fallbackDeadline;
  if (status.attempt?.state === "expired" || !Number.isFinite(deadline) || deadline <= now) return 0;
  return Math.min(2000, deadline - now);
}
