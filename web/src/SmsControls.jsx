import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { usePresence } from "./usePresence";
import { phoneConnectionState } from "./phoneConnection";

async function connectionJson(path) {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, { signal: controller.signal });
    if (!response.ok) throw new Error("Phone alerts are unavailable.");
    return await response.json();
  } finally { clearTimeout(deadline); }
}

const terminal = new Set(["delivered", "failed", "undelivered", "canceled", "rejected", "uncertain", "suppressed", "rate_limited"]);
const retrySameEvent = new Set(["not_found", "not_configured", "disabled", "suppressed", "rate_limited"]);
const resultText = (result) => {
  const status = result.provider_status || result.status;
  if (result.receipt_error) return `Status unavailable. Last reported: ${String(status || "unknown").replaceAll("_", " ")}. Check again; this won’t resend.`;
  if (status === "delivered") return "Twilio reports delivered.";
  if (["provider_accepted", "queued", "accepted", "sending", "sent"].includes(status)) return `Twilio: ${status}. Phone delivery is still unconfirmed.`;
  if (status === "uncertain") return "Outcome unknown. Check Twilio before sending again.";
  if (["failed", "undelivered", "rejected"].includes(status)) return `Message ${status}${result.error_code ? ` (${result.error_code})` : ""}. Check Twilio setup.`;
  return `SMS: ${String(status || "unavailable").replaceAll("_", " ")}${result.error_code ? ` (${result.error_code})` : ""}.`;
};

export default function SmsControls({ onStatus, onAssessment, orchard }) {
  const [open, setOpen] = useState(false), [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false), [result, setResult] = useState(null), [error, setError] = useState("");
  const [subscribed, setSubscribed] = useState(null), [subscriptionBusy, setSubscriptionBusy] = useState(false);
  const [connecting, setConnecting] = useState(false), [connectionAttempted, setConnectionAttempted] = useState(false), [connectionError, setConnectionError] = useState("");
  const [hasDemoEvent, setHasDemoEvent] = useState(() => { try { return Boolean(sessionStorage.getItem("terra-sms-demo-event")); } catch { return false; } });
  const panelMounted = usePresence(open, 180);
  const container = useRef(null), subscriptionVersion = useRef(0), connectionVersion = useRef(0);
  const eventId = useRef(null), trigger = useRef(null), closeButton = useRef(null), alive = useRef(true);
  const loadStatus = useCallback(async () => {
    const version = ++connectionVersion.current;
    try {
      const value = await connectionJson("/api/sms/status");
      if (typeof value.configured !== "boolean" || typeof value.control_token !== "string") throw new Error("SMS configuration is unavailable.");
      if (alive.current && version === connectionVersion.current) { setStatus(value); onStatus?.(value); setConnectionError(""); setError(""); }
      return value;
    } catch (reason) { if (alive.current && version === connectionVersion.current) setConnectionError(reason.message); return null; }
  }, [onStatus]);
  useEffect(() => { alive.current = true; loadStatus(); return () => { alive.current = false; connectionVersion.current += 1; }; }, [loadStatus]);
  useEffect(() => { if (open) closeButton.current?.focus(); }, [open]);
  const loadSubscription = useCallback(async () => {
    const version = ++subscriptionVersion.current;
    if (!orchard?.id) { setSubscribed(null); return; }
    try {
      const value = await connectionJson("/api/orchards/" + encodeURIComponent(orchard.id) + "/sms-opt-in");
      if (typeof value.enabled !== "boolean") throw new Error();
      if (alive.current && version === subscriptionVersion.current) setSubscribed(value.enabled);
    } catch { if (alive.current && version === subscriptionVersion.current) setSubscribed(null); }
  }, [orchard?.id]);
  useEffect(() => { setSubscribed(null); loadSubscription(); return () => { subscriptionVersion.current += 1; }; }, [loadSubscription]);
  useEffect(() => {
    if (!open) return;
    function outside(event) { if (!container.current?.contains(event.target)) setOpen(false); }
    function escape(event) { if (event.key === "Escape") { event.preventDefault(); close(); } }
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  function restoreControlFocus(origin, hadFocus) {
    requestAnimationFrame(() => {
      if (!hadFocus || !alive.current || !container.current?.querySelector('[data-open="true"]') || (document.activeElement !== document.body && document.activeElement !== origin)) return;
      const target = origin?.isConnected && !origin.disabled ? origin : container.current.querySelector('.sms-actions button:not(:disabled)');
      target?.focus();
    });
  }
  async function changeSubscription(event) {
    const enabled = event.target.checked;
    if (!orchard?.id || subscriptionBusy) return;
    const origin = event.currentTarget, hadFocus = document.activeElement === origin;
    const version = ++subscriptionVersion.current;
    setSubscriptionBusy(true); setError("");
    try {
      const current = await loadStatus();
      if (!current?.control_token) throw new Error("Reload setup to authorize this change.");
      const response = await fetch("/api/orchards/" + encodeURIComponent(orchard.id) + "/sms-opt-in", { method: "PUT", headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": current.control_token }, body: JSON.stringify({ enabled }) });
      if (!response.ok) throw new Error("The phone preference could not be saved. Retry setup.");
      const value = await response.json();
      if (value.enabled !== enabled) throw new Error("The phone preference could not be confirmed.");
      if (alive.current && version === subscriptionVersion.current) setSubscribed(enabled);
    } catch (reason) { if (alive.current) setError(reason.message); }
    finally { if (alive.current) { setSubscriptionBusy(false); restoreControlFocus(origin, hadFocus); } }
  }
  async function refreshSetup(event) {
    if (connecting) return;
    const origin = event?.currentTarget;
    const hadFocus = origin && document.activeElement === origin;
    setConnecting(true); setConnectionAttempted(true);
    try { await Promise.all([loadStatus(), loadSubscription()]); }
    finally {
      if (alive.current) {
        setConnecting(false);
        restoreControlFocus(origin, hadFocus);
      }
    }
  }
  const connection = phoneConnectionState(status, { checking: connecting, attempted: connectionAttempted, unavailable: Boolean(connectionError) });
  const ready = connection.kind === "connected" && status?.demo_enabled === true;
  const trialTemplate = status?.content_mode === "trial_template";
  function close() { setOpen(false); requestAnimationFrame(() => trigger.current?.focus()); }
  async function send(event) {
    if (!ready || busy) return;
    const origin = event?.currentTarget, hadFocus = document.activeElement === origin;
    // Retain the ID after a lost response: checking/retrying the same event cannot
    // silently create a second logical send, including across page reloads.
    if (!eventId.current) {
      try { eventId.current = sessionStorage.getItem("terra-sms-demo-event"); } catch { /* storage may be disabled */ }
      if (!eventId.current) eventId.current = crypto.randomUUID();
      try { sessionStorage.setItem("terra-sms-demo-event", eventId.current); } catch { /* in-memory ID still deduplicates */ }
    }
    setHasDemoEvent(true);
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/sms/demo", { method: "POST", headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": status.control_token }, body: JSON.stringify({ event_id: eventId.current }) });
      const value = await response.json();
      if (!response.ok || value.error) throw new Error("SMS could not be confirmed. Check delivery status before starting another demo.");
      if (alive.current) { setResult(value); if (value.assessment) onAssessment(value.assessment); }
    } catch (reason) { if (alive.current) setError(reason.message || "SMS outcome is unknown. Check delivery status."); }
    finally { if (alive.current) { setBusy(false); restoreControlFocus(origin, hadFocus); } }
  }
  async function checkReceipt(event) {
    if (busy || !status?.control_token) return;
    const origin = event?.currentTarget, hadFocus = document.activeElement === origin;
    if (!eventId.current) {
      try { eventId.current = sessionStorage.getItem("terra-sms-demo-event"); } catch { /* no stored event */ }
    }
    if (!eventId.current) { setError("Send a demo first to create a delivery record."); return; }
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/sms/receipt/${encodeURIComponent(eventId.current)}`, { headers: { "X-Terra-SMS-Control": status.control_token } });
      const value = await response.json();
      if (!response.ok || value.error) throw new Error("Delivery status could not be checked. Try checking again; this does not resend the message.");
      if (alive.current) setResult(value);
    } catch (reason) { if (alive.current) setError(reason.message); }
    finally { if (alive.current) { setBusy(false); restoreControlFocus(origin, hadFocus); } }
  }
  async function stop(event) {
    if (busy || !status?.control_token) return;
    const origin = event?.currentTarget, hadFocus = document.activeElement === origin;
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/sms/disable", { method: "POST", headers: { "X-Terra-SMS-Control": status.control_token } });
      const value = await response.json();
      if (!response.ok || value.suppressed !== true) throw new Error("SMS could not be paused. Alerts may still be enabled; check the server configuration.");
      await loadStatus();
    } catch (reason) { if (alive.current) setError(reason.message); }
    finally { if (alive.current) { setBusy(false); restoreControlFocus(origin, hadFocus); } }
  }
  function newDemo(event) {
    const origin = event?.currentTarget, hadFocus = document.activeElement === origin;
    eventId.current = null; setHasDemoEvent(false); setResult(null); setError("");
    try { sessionStorage.removeItem("terra-sms-demo-event"); } catch { /* no storage */ }
    restoreControlFocus(origin, hadFocus);
  }
  return <div ref={container} className="sms-controls">
    <button ref={trigger} className="icon-button sms-toggle" aria-label="Phone alerts" title="Phone alerts" aria-expanded={open} aria-controls="sms-controls-panel" onClick={() => setOpen((value) => !value)}><Icon name="phone" size={16} /></button>
    {panelMounted && <section id="sms-controls-panel" className="query-panel sms-panel" data-open={open} inert={!open ? true : undefined} aria-label="Phone alerts" onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); close(); } }}>
      <div className="query-heading"><strong>Phone alerts</strong><span className="sms-connection-status" data-state={connection.kind} role="status">{connection.kind === "connected" && <Icon name="check" size={12} />}{connection.label}</span><button ref={closeButton} type="button" className="icon-button" onClick={close} aria-label="Close phone alerts"><Icon name="close" size={15} /></button></div>
      {typeof status?.recipient === "string" && status.recipient && status.configured && <p className="sms-connected-number" aria-label={`Phone ending in ${status.recipient.slice(-4)}`}>{status.recipient.replace(/^\*+/, "••• ••• ")}</p>}
      <p className="sms-description" hidden={!connection.copy}>{connection.copy}</p>
      {orchard && status?.configured && (status.enabled || subscribed === true) && <div className="sms-preference">{subscribed === null ? <div className="sms-preference-unavailable"><span>Alert preference unavailable</span><button type="button" className="sms-text-button" onClick={refreshSetup} disabled={connecting}>Retry</button></div> : <label><span className="sms-preference-copy"><span>Field note alerts</span><small>{orchard.name}{orchard.demo_location && !/demo/i.test(orchard.name) ? " · demo" : ""}</small></span><input type="checkbox" role="switch" className="sms-switch" aria-label={`Field note alerts for ${orchard.name}`} checked={subscribed} disabled={busy || connecting || subscriptionBusy || (!subscribed && connection.kind !== "connected")} onChange={changeSubscription} /></label>}{subscriptionBusy && <p className="microcopy" role="status">Updating preference…</p>}</div>}
      {result && <p className="sms-result" role="status">{resultText(result)}{result.duplicate ? " Already recorded; no repeat sent." : ""}</p>}
      {error && <p className="query-error" role="alert">{error}</p>}
      {status?.configured && (connection.kind === "connected" || hasDemoEvent || result) && <div className="sms-test"><div className="sms-demo-label">{trialTemplate ? "Trial notification" : "Simulated alert"}</div>{connection.kind === "connected" && !status.demo_enabled && <p className="microcopy">Test sending is disabled.</p>}<div className="sms-actions">{connection.kind === "connected" && <button type="button" className="query-submit" onClick={send} disabled={!ready || busy || Boolean(result && !retrySameEvent.has(result.status))}>{busy ? "Working…" : result && retrySameEvent.has(result.status) ? "Retry test" : trialTemplate ? "Send test notification" : "Send test alert"}</button>}{(hasDemoEvent || result) && <button type="button" className="query-cancel" onClick={checkReceipt} disabled={busy}>Check delivery</button>}</div>{result && terminal.has(result.provider_status || result.status) && result.status !== "uncertain" && <button type="button" className="sms-text-button" onClick={newDemo} disabled={busy}>New test</button>}{ready && <p className="microcopy">{trialTemplate ? "Notification only. Read the field note in Terra. SMS charges may apply." : "Uses simulated readings. SMS charges may apply."}</p>}</div>}
      <div className={`sms-actions sms-secondary${connection.kind === "connected" ? "" : " sms-connect-actions"}`}><button type="button" className={connection.kind === "connected" ? "sms-text-button" : "query-submit"} onClick={refreshSetup} disabled={busy || subscriptionBusy || connection.kind === "checking"} aria-busy={connecting}>{connection.action}</button>{status?.enabled && !status?.suppressed && <button type="button" className="sms-text-button" onClick={stop} disabled={busy}>Pause all texts</button>}</div>
    </section>}
  </div>;
}
