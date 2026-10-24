import React, { useCallback, useEffect, useRef, useState } from "react";

const terminal = new Set(["delivered", "failed", "undelivered", "canceled", "rejected", "uncertain", "suppressed", "rate_limited"]);
const retrySameEvent = new Set(["not_found", "not_configured", "disabled", "suppressed", "rate_limited"]);
const resultText = (result) => {
  const status = result.provider_status || result.status;
  if (result.receipt_error) return `Delivery status could not be refreshed (${result.receipt_error}). Last reported state: ${String(status || "unknown").replaceAll("_", " ")}. Checking again will not resend.`;
  if (status === "delivered") return "Twilio reports delivered. Check your phone for the message labelled TERRA DEMO.";
  if (["queued", "accepted", "sending", "sent"].includes(status)) return `Twilio: ${status}. Phone delivery is still unconfirmed.`;
  if (status === "uncertain") return "The send outcome is uncertain. Check Twilio’s message log before sending a new demo.";
  if (["failed", "undelivered", "rejected"].includes(status)) return `Message ${status}${result.error_code ? ` (${result.error_code})` : ""}. Check your Twilio sender and account readiness.`;
  return `SMS: ${String(status || "unavailable").replaceAll("_", " ")}${result.error_code ? ` (${result.error_code})` : ""}.`;
};

export default function SmsControls({ onStatus, onAssessment }) {
  const [open, setOpen] = useState(false), [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false), [result, setResult] = useState(null), [error, setError] = useState("");
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
  return <div className="sms-controls">
    <button ref={trigger} className="query-toggle sms-toggle" aria-expanded={open} aria-controls="sms-controls-panel" onClick={() => setOpen((value) => !value)}><span>Phone alerts</span><small>{status?.enabled && !status?.suppressed ? "SMS enabled" : "Setup & demo"}</small></button>
    {open && <section id="sms-controls-panel" className="query-panel sms-panel" aria-label="Phone alerts" onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); close(); } }}>
      <div className="query-heading"><strong>Phone alerts</strong><button ref={closeButton} type="button" className="icon-button" onClick={close} aria-label="Close phone alerts">×</button></div>
      <p className="sms-description">Simulate a temperature flag and send its evidence to your phone. The text is clearly labelled as a demo.</p>
      {status?.configured ? <p className="sms-state">To {status.recipient} · {status.suppressed ? "Paused" : status.enabled ? "Enabled" : "Disabled"}</p> : <p className="sms-state">Twilio needs an approved SMS sender, credentials and your phone number in the server’s private configuration.</p>}
      {status?.enabled && !status?.suppressed && <p className="microcopy">The monitor sends selected orchard updates. Opening a saved assessment or checking a location does not send a text.</p>}
      {status?.configured && !status?.demo_enabled && <p className="microcopy">Demo delivery is disabled in the server configuration.</p>}
      {result && <p className="sms-result" role="status">{resultText(result)}{result.duplicate ? " This event was already recorded; no second message was sent." : ""}</p>}
      {error && <p className="query-error" role="alert">{error}</p>}
      <div className="sms-actions"><button type="button" className="query-submit" onClick={send} disabled={!ready || busy || Boolean(result && !retrySameEvent.has(result.status))}>{busy ? "Working…" : result && retrySameEvent.has(result.status) ? "Retry this demo" : "Send simulated flag to my phone"}</button><button type="button" className="query-cancel" onClick={checkReceipt} disabled={busy || !status?.configured}>Check delivery</button></div>
      {result && terminal.has(result.provider_status || result.status) && result.status !== "uncertain" && <button type="button" className="sms-text-button" onClick={newDemo} disabled={busy}>Prepare another demo</button>}
      <div className="sms-actions sms-secondary"><button type="button" className="sms-text-button" onClick={loadStatus} disabled={busy}>Refresh setup</button>{status?.enabled && !status?.suppressed && <button type="button" className="sms-text-button" onClick={stop} disabled={busy}>Pause all phone alerts</button>}</div>
      <p className="microcopy">Twilio may charge for messages. A queued or sent result does not confirm phone delivery.</p>
    </section>}
  </div>;
}
