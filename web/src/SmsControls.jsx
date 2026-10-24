import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { usePresence } from "./usePresence";
import { demoResultText } from "./delivery";

const terminal = new Set(["delivered", "failed", "undelivered", "canceled", "rejected", "uncertain", "suppressed", "rate_limited"]);
const retrySameEvent = new Set(["not_found", "not_configured", "disabled", "suppressed", "rate_limited"]);

export default function SmsControls({ onStatus, onAssessment, orchard }) {
  const [open, setOpen] = useState(false), [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false), [result, setResult] = useState(null), [error, setError] = useState("");
  const [subscribed, setSubscribed] = useState(null), [subscriptionBusy, setSubscriptionBusy] = useState(false);
  const panelMounted = usePresence(open, 180);
  const container = useRef(null), subscriptionVersion = useRef(0);
  const eventId = useRef(null), trigger = useRef(null), closeButton = useRef(null), alive = useRef(true);
  const loadStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/sms/status");
      if (!response.ok) throw new Error("SMS controls are unavailable. Open the local Terra server to connect Twilio.");
      const value = await response.json();
      if (typeof value.configured !== "boolean" || typeof value.control_token !== "string") throw new Error("SMS configuration is unavailable.");
      if (alive.current) { setStatus(value); onStatus?.(value); setError(""); }
      return value;
    } catch (reason) { if (alive.current) setError(reason.message); return null; }
  }, [onStatus]);
  useEffect(() => { alive.current = true; loadStatus(); return () => { alive.current = false; }; }, [loadStatus]);
  useEffect(() => { if (open) closeButton.current?.focus(); }, [open]);
  const loadSubscription = useCallback(async () => {
    const version = ++subscriptionVersion.current;
    if (!orchard?.id) { setSubscribed(null); return; }
    try {
      const response = await fetch("/api/orchards/" + encodeURIComponent(orchard.id) + "/sms-opt-in");
      if (!response.ok) throw new Error();
      const value = await response.json();
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
  async function changeSubscription(event) {
    const enabled = event.target.checked;
    if (!orchard?.id || subscriptionBusy) return;
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
    finally { if (alive.current) setSubscriptionBusy(false); }
  }
  async function refreshSetup() { await loadStatus(); await loadSubscription(); }
  const ready = status?.configured && status?.enabled && status?.demo_enabled && !status?.suppressed;
  function close() { setOpen(false); requestAnimationFrame(() => trigger.current?.focus()); }
  async function send() {
    if (!ready || busy) return;
    // Retain the ID after a lost response: checking/retrying the same event cannot
    // silently create a second logical send, including across page reloads.
    if (!eventId.current) {
      try { eventId.current = sessionStorage.getItem("terra-sms-demo-event"); } catch { /* storage may be disabled */ }
      if (!eventId.current) eventId.current = crypto.randomUUID();
      try { sessionStorage.setItem("terra-sms-demo-event", eventId.current); } catch { /* in-memory ID still deduplicates */ }
    }
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/sms/demo", { method: "POST", headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": status.control_token }, body: JSON.stringify({ event_id: eventId.current }) });
      const value = await response.json();
      if (!response.ok || value.error) throw new Error("SMS could not be confirmed. Check delivery status before starting another demo.");
      if (alive.current) { setResult(value); if (value.assessment) onAssessment(value.assessment); }
    } catch (reason) { if (alive.current) setError(reason.message || "SMS outcome is unknown. Check delivery status."); }
    finally { if (alive.current) setBusy(false); }
  }
  async function checkReceipt() {
    if (busy || !status?.control_token) return;
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
    finally { if (alive.current) setBusy(false); }
  }
  async function stop() {
    if (busy || !status?.control_token) return;
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/sms/disable", { method: "POST", headers: { "X-Terra-SMS-Control": status.control_token } });
      const value = await response.json();
      if (!response.ok || value.suppressed !== true) throw new Error("SMS could not be paused. Alerts may still be enabled; check the server configuration.");
      await loadStatus();
    } catch (reason) { if (alive.current) setError(reason.message); }
    finally { if (alive.current) setBusy(false); }
  }
  function newDemo() {
    eventId.current = null; setResult(null); setError("");
    try { sessionStorage.removeItem("terra-sms-demo-event"); } catch { /* no storage */ }
  }
  return <div ref={container} className="sms-controls">
    <button ref={trigger} className="icon-button sms-toggle" aria-label="Phone alerts" title="Phone alerts" aria-expanded={open} aria-controls="sms-controls-panel" onClick={() => setOpen((value) => !value)}><Icon name="phone" size={16} /></button>
    {panelMounted && <section id="sms-controls-panel" className="query-panel sms-panel" data-open={open} inert={!open ? true : undefined} aria-label="Phone alerts" onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); close(); } }}>
      <div className="query-heading"><strong>Phone alerts</strong><button ref={closeButton} type="button" className="icon-button" onClick={close} aria-label="Close phone alerts"><Icon name="close" size={15} /></button></div>

      {status?.configured ? <p className="sms-state">To {status.recipient} · {status.suppressed ? "Paused" : status.enabled ? "Enabled" : "Disabled"}</p> : <p className="sms-state">Phone alerts aren’t connected yet.</p>}
      {orchard && <div className="sms-orchard"><label><input type="checkbox" checked={subscribed === true} disabled={busy || subscriptionBusy || subscribed === null || (!subscribed && (!status?.configured || !status?.enabled || status?.suppressed))} onChange={changeSubscription} /><span>Phone updates for {orchard.name}{orchard.demo_location ? " · demo" : ""}</span></label>{subscribed === null && <p className="microcopy">Subscription unavailable. Refresh setup to retry.</p>}</div>}
      <p className="microcopy">The monitor sends opted-in orchard updates.</p>
      {status?.content_mode === "trial_template" && <p className="microcopy">Twilio sample SMS only. Open Terra for the actual finding.</p>}
      <div className="sms-demo-label">Simulated flag · explicit test</div>
      {status?.configured && !status?.demo_enabled && <p className="microcopy">Demo delivery is disabled in the server configuration.</p>}
      {result && <p className="sms-result" role="status">{demoResultText(result)}{result.duplicate ? " This event was already recorded; no second message was sent." : ""}</p>}
      {error && <p className="query-error" role="alert">{error}</p>}
      <div className="sms-actions"><button type="button" className="query-submit" onClick={send} disabled={!ready || busy || Boolean(result && !retrySameEvent.has(result.status))}>{busy ? "Working…" : result && retrySameEvent.has(result.status) ? "Retry this demo" : status?.content_mode === "trial_template" ? "Send trial placeholder to my phone" : "Send simulated flag to my phone"}</button><button type="button" className="query-cancel" onClick={checkReceipt} disabled={busy || !status?.configured}>Check delivery</button></div>
      {result && terminal.has(result.provider_status || result.status) && result.status !== "uncertain" && <button type="button" className="sms-text-button" onClick={newDemo} disabled={busy}>Prepare another demo</button>}
      <div className="sms-actions sms-secondary"><button type="button" className="sms-text-button" onClick={refreshSetup} disabled={busy || subscriptionBusy}>Refresh setup</button>{status?.enabled && !status?.suppressed && <button type="button" className="sms-text-button" onClick={stop} disabled={busy}>Pause all phone alerts</button>}</div>
      <p className="microcopy">Twilio may charge for messages. A queued or sent result does not confirm phone delivery.</p>
    </section>}
  </div>;
}
