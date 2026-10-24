import React, { useCallback, useEffect, useRef, useState } from "react";
import { UserCircle } from "@phosphor-icons/react";
import { Icon } from "./Icon";
import { usePresence } from "./usePresence";
import { authorizationUrl, CHATGPT_USAGE_URL, connectionError, connectionPhase, connectionPollDelay, connectionStatus, modelCatalog } from "./chatgpt-state";
import "./chatgpt.css";

const CONFIRMED_ACCOUNTS = "terra-chatgpt-plan-confirmed";
const errorText = (reason, fallback) => reason?.name === "Error" ? reason.message : fallback;
function confirmedAccounts() {
  try {
    const value = JSON.parse(localStorage.getItem(CONFIRMED_ACCOUNTS) || "[]");
    return new Set(Array.isArray(value) ? value.filter((id) => typeof id === "string") : []);
  } catch { return new Set(); }
}

export default function ChatGPTConnection() {
  const [open, setOpen] = useState(false), [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState("");
  const [error, setError] = useState(""), [catalogError, setCatalogError] = useState("");
  const [catalog, setCatalog] = useState(null), [catalogLoading, setCatalogLoading] = useState(false);
  const [authLink, setAuthLink] = useState(""), [expiredAttempt, setExpiredAttempt] = useState(null);
  const [confirmed, setConfirmed] = useState(confirmedAccounts), [pollTick, setPollTick] = useState(0);
  const mounted = usePresence(open, 180);
  const container = useRef(null), trigger = useRef(null), firstFocus = useRef(null);
  const alive = useRef(false), lifecycle = useRef(null), controlToken = useRef(null);
  const statusVersion = useRef(0), catalogVersion = useRef(0), activeAccount = useRef(null), busyRef = useRef(false), popup = useRef(null);
  const pollDeadline = useRef({ id: null, deadline: 0 });

  const api = useCallback(async (path, { method = "GET", body, fresh = false } = {}) => {
    const signal = lifecycle.current?.signal;
    if (fresh || !controlToken.current) {
      const response = await fetch("/api/sms/status", { signal, cache: "no-store" });
      const value = await response.json().catch(() => null);
      if (!response.ok || typeof value?.control_token !== "string" || !value.control_token) throw new Error("Message writing is unavailable. Open the local Terra server and try again.");
      controlToken.current = value.control_token;
    }
    const response = await fetch(`/api/chatgpt/${path}`, {
      method, signal, cache: "no-store",
      headers: { "X-Terra-SMS-Control": controlToken.current, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const value = await response.json().catch(() => null);
    if (!response.ok || value?.error) {
      const error = connectionError(response.status, value);
      if (error.localSession) controlToken.current = null;
      throw error;
    }
    return value;
  }, []);

  const loadStatus = useCallback(async ({ fresh = false, quiet = false } = {}) => {
    const version = ++statusVersion.current;
    if (!quiet && alive.current) setLoading(true);
    try {
      const value = connectionStatus(await api("status", { fresh }));
      if (alive.current && statusVersion.current === version) {
        setStatus(value); setError("");
        if (value.state !== "connecting") { setAuthLink(""); setExpiredAttempt(null); }
      }
      return value;
    } catch (reason) {
      if (alive.current && statusVersion.current === version && reason.name !== "AbortError") setError(errorText(reason, quiet ? "The connection couldn’t be checked. Template notes still work." : "Message writing is unavailable. Template notes still work."));
      throw reason;
    } finally { if (alive.current && statusVersion.current === version) setLoading(false); }
  }, [api]);

  useEffect(() => {
    alive.current = true; lifecycle.current = new AbortController();
    loadStatus({ fresh: true }).catch(() => {});
    return () => { alive.current = false; lifecycle.current.abort(); statusVersion.current += 1; controlToken.current = null; };
  }, [loadStatus]);

  const refresh = useCallback(() => {
    if (busyRef.current) return;
    setError("");
    loadStatus({ fresh: true }).catch(() => {});
  }, [loadStatus]);

  function close(restoreFocus = true) {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => trigger.current?.focus());
  }
  useEffect(() => {
    if (!open) return;
    firstFocus.current?.focus();
    refresh();
    const outside = (event) => { if (!container.current?.contains(event.target)) close(false); };
    const escape = (event) => { if (event.key === "Escape") { event.preventDefault(); close(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open, refresh]);

  const attemptId = status?.attempt?.id || "pending";
  const phase = expiredAttempt === attemptId && status?.state === "connecting" ? "expired" : connectionPhase(status);
  useEffect(() => {
    if (!open || busy || phase !== "connecting") return;
    if (pollDeadline.current.id !== attemptId) pollDeadline.current = { id: attemptId, deadline: Date.now() + 120000 };
    const delay = connectionPollDelay(status, Date.now(), pollDeadline.current.deadline);
    if (delay === 0) { setExpiredAttempt(attemptId); return; }
    let canceled = false;
    const timer = setTimeout(async () => {
      if (connectionPollDelay(status, Date.now(), pollDeadline.current.deadline) === 0) { setExpiredAttempt(attemptId); return; }
      try { await loadStatus({ quiet: true }); }
      catch { /* loadStatus only reports errors from the current request. */ }
      finally { if (!canceled && alive.current) setPollTick((value) => value + 1); }
    }, delay);
    return () => { canceled = true; clearTimeout(timer); };
  }, [open, busy, phase, status, attemptId, pollTick, loadStatus]);

  const accountId = status?.active_account_id || null;
  activeAccount.current = accountId;
  const selectedModel = status?.writer?.selected_model;
  const hasPlan = status?.plan_enabled && (status?.state === "connected" || phase === "paused");
  const loadCatalog = useCallback(async (fresh = false) => modelCatalog(await api("models", { fresh })), [api]);
  useEffect(() => {
    let canceled = false;
    const version = ++catalogVersion.current;
    setCatalog(null); setCatalogError("");
    if (!hasPlan || !accountId) { setCatalogLoading(false); return; }
    setCatalogLoading(true);
    loadCatalog().then((value) => { if (!canceled && alive.current && catalogVersion.current === version && activeAccount.current === accountId) setCatalog(value); })
      .catch((reason) => { if (!canceled && alive.current && catalogVersion.current === version && activeAccount.current === accountId && reason.name !== "AbortError") setCatalogError("Models could not be loaded. Try again."); })
      .finally(() => { if (!canceled && alive.current && catalogVersion.current === version && activeAccount.current === accountId) setCatalogLoading(false); });
    return () => { canceled = true; };
  }, [hasPlan, accountId, selectedModel, loadCatalog]);

  async function act(kind, path, body = {}, method = "POST") {
    if (busyRef.current) return false;
    busyRef.current = true; statusVersion.current += 1;
    setBusy(kind); setError("");
    try {
      await api(path, { method, body, fresh: true });
      await loadStatus();
      return true;
    } catch (reason) { if (alive.current && reason.name !== "AbortError") setError(errorText(reason, "Message writing couldn’t be updated. Try again.")); return false; }
    finally { busyRef.current = false; if (alive.current) { setBusy(""); setLoading(false); } }
  }

  async function connect({ account = null, reconsent = false } = {}) {
    if (busyRef.current) return;
    busyRef.current = true; statusVersion.current += 1;
    setBusy("connect"); setError(""); setAuthLink(""); setExpiredAttempt(null);
    // Reserve a window inside the click event; browsers may block one opened after fetch.
    try { popup.current = window.open("about:blank", "_blank", "popup,width=540,height=720"); if (popup.current) popup.current.opener = null; } catch { popup.current = null; }
    let opened = false;
    try {
      const value = await api("connect", { method: "POST", body: { ...(account ? { account_id: account } : {}), ...(reconsent ? { reconsent: true } : {}) }, fresh: true });
      const url = authorizationUrl(value?.authorization_url);
      if (typeof value.attempt_id !== "string" || !value.attempt_id || !Number.isFinite(Date.parse(value.expires_at))) throw new Error("The connection could not be started. Try again.");
      if (!alive.current) return;
      setAuthLink(url);
      setStatus((current) => ({ ...current, state: "connecting", attempt: { id: value.attempt_id, state: "connecting", expires_at: value.expires_at } }));
      pollDeadline.current = { id: value.attempt_id, deadline: Date.now() + 120000 };
      if (popup.current && !popup.current.closed) { try { popup.current.location.replace(url); opened = true; } catch { /* The visible link below remains available. */ } }
      await loadStatus();
    } catch (reason) { if (alive.current && reason.name !== "AbortError") setError(errorText(reason, "ChatGPT couldn’t connect. Try again.")); }
    finally {
      if (!opened) { try { popup.current?.close(); } catch { /* A blocked window has no handle. */ } }
      busyRef.current = false;
      if (alive.current) { setBusy(""); setLoading(false); }
    }
  }

  async function cancel() {
    if (await act("cancel", "cancel", status?.attempt?.id ? { attempt_id: status.attempt.id } : {})) {
      try { popup.current?.close(); } catch { /* The user may already have closed it. */ }
      setAuthLink(""); setExpiredAttempt(null);
    }
  }
  async function chooseModel(event) {
    const model = event.target.value;
    const forAccount = accountId;
    if (!catalog?.models.some((item) => item.slug === model)) return;
    if (await act("model", "model", { model }, "PUT") && activeAccount.current === forAccount) {
      const version = ++catalogVersion.current;
      try { const value = await loadCatalog(); if (alive.current && catalogVersion.current === version && activeAccount.current === forAccount) { setCatalog(value); setCatalogError(""); } }
      catch { if (alive.current && catalogVersion.current === version && activeAccount.current === forAccount) setCatalogError("Models could not be loaded. Try again."); }
      finally { if (alive.current && catalogVersion.current === version && activeAccount.current === forAccount) setCatalogLoading(false); }
    }
  }
  function confirmPlan() {
    if (!accountId) return;
    const next = new Set(confirmed); next.add(accountId); setConfirmed(next);
    // Only opaque account IDs and this acknowledgement preference are persisted.
    try { localStorage.setItem(CONFIRMED_ACCOUNTS, JSON.stringify([...next])); } catch { /* The acknowledgement lasts for this page session. */ }
  }
  async function retryModels() {
    if (busyRef.current || catalogLoading) return;
    const version = ++catalogVersion.current, forAccount = accountId;
    setCatalogLoading(true); setCatalogError("");
    try { const value = await loadCatalog(true); if (alive.current && catalogVersion.current === version && activeAccount.current === forAccount) setCatalog(value); }
    catch { if (alive.current && catalogVersion.current === version && activeAccount.current === forAccount) setCatalogError("Models could not be loaded. Try again."); }
    finally { if (alive.current && catalogVersion.current === version && activeAccount.current === forAccount) setCatalogLoading(false); }
  }

  const account = status?.accounts?.find((item) => item.id === accountId);
  const accounts = status?.accounts || [];
  const model = catalog?.models.some((item) => item.slug === catalog.selected_model) ? catalog.selected_model : "";
  const canManage = Boolean(accountId) && phase !== "connecting";
  const connectLabel = "Continue with ChatGPT";

  return <div className="chatgpt-controls" ref={container}>
    <button ref={trigger} type="button" className="icon-button chatgpt-toggle" aria-label="Message writing" title="Message writing" aria-haspopup="dialog" aria-expanded={open} aria-controls="chatgpt-connection-panel" onClick={() => setOpen((value) => !value)}><UserCircle size={18} weight="regular" aria-hidden="true" /></button>
    {mounted && <section id="chatgpt-connection-panel" className="query-panel chatgpt-panel" data-open={open} inert={!open ? true : undefined} role="dialog" aria-label="Message writing" aria-busy={Boolean(busy) || loading}>
      <div className="query-heading"><strong>Message writing</strong><button ref={firstFocus} type="button" className="icon-button" aria-label="Close message writing" onClick={() => close()}><Icon name="close" size={15} /></button></div>
      {status?.preview_only && <p className="microcopy">Connection preview. Saved field notes stay unchanged.</p>}

      {phase === "unavailable" ? <p className="chatgpt-state">{loading ? "Checking message writing…" : "Template notes still work."}</p>
        : phase === "connecting" ? <p className="chatgpt-state" role="status">Finish connecting in your browser.</p>
          : phase === "connected" ? <><p className="chatgpt-state">Using ChatGPT plan</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
            : phase === "paused" ? <><p className="chatgpt-state">ChatGPT writing is paused at its usage limit. Template notes still work.</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
              : phase === "plan_disabled" ? <><p className="chatgpt-state">Signed in, plan use not enabled.</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
                : phase === "reauth_required" ? <><p className="chatgpt-state">Reconnect ChatGPT to write new notes. Template notes still work.</p>{account && <p className="chatgpt-account">{account.label}</p>}</>
                  : phase === "expired" ? <p className="chatgpt-state">The connection link expired. Reconnect to try again.</p>
                    : phase === "error" ? <p className="chatgpt-state">ChatGPT couldn’t connect. Template notes still work.</p>
                      : <p className="chatgpt-state">Write grower notes with your ChatGPT plan.</p>}

      {hasPlan && accountId && !confirmed.has(accountId) && <div className="chatgpt-confirmation"><p>You’re using your ChatGPT plan.</p><button type="button" onClick={confirmPlan}>Got it</button></div>}

      {accounts.length > 1 && phase !== "connecting" && <label className="chatgpt-field">Account<select value={accountId || ""} disabled={Boolean(busy) || loading} onChange={(event) => act("account", "select-account", { account_id: event.target.value })}><option value="" disabled>Choose an account</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}

      {hasPlan && <div className="chatgpt-model"><label className="chatgpt-field">Model<select value={model} disabled={Boolean(busy) || loading || catalogLoading || !catalog?.models.length} onChange={chooseModel}><option value="" disabled>{catalogLoading ? "Loading models…" : "Choose a model"}</option>{catalog?.models.map((item) => <option key={item.slug} value={item.slug}>{item.display_name}</option>)}</select></label>{catalogError ? <p className="chatgpt-model-note">{catalogError} <button type="button" onClick={retryModels} disabled={Boolean(busy) || catalogLoading}>Retry</button></p> : !catalogLoading && !model && <p className="chatgpt-model-note">{catalog?.models.length ? "Choose a model for new notes." : "No models are available for this account."}</p>}</div>}

      {error && <p className="query-error" role="alert">{error}</p>}
      <div className="chatgpt-actions">
        {phase === "unavailable" ? <button type="button" className="query-cancel" disabled={loading || Boolean(busy)} onClick={refresh}>Try again</button>
          : phase === "connecting" ? <>{authLink && <a className="query-submit" href={authLink} target="_blank" rel="noopener noreferrer">Open connection</a>}<button type="button" className="query-cancel" disabled={Boolean(busy)} onClick={cancel}>{busy === "cancel" ? "Canceling…" : "Cancel"}</button></>
            : phase === "paused" ? <><a className="query-submit" href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer">Manage usage</a><button type="button" className="query-cancel" disabled={Boolean(busy) || loading} onClick={() => act("resume", "resume")}>{busy === "resume" ? "Resuming…" : "Resume"}</button></>
              : phase === "connected" ? <a className="chatgpt-text-button" href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer">Manage usage</a>
                : <button type="button" className="query-submit" disabled={Boolean(busy) || loading} onClick={() => connect({ account: accountId, reconsent: phase === "plan_disabled" })}>{busy === "connect" ? "Connecting…" : connectLabel}</button>}
      </div>
      {canManage && <div className="chatgpt-account-actions"><button type="button" className="chatgpt-text-button" disabled={Boolean(busy) || loading} onClick={() => connect()}>Add account</button><button type="button" className="chatgpt-text-button" disabled={Boolean(busy) || loading} onClick={() => act("disconnect", "disconnect", { account_id: accountId })}>{busy === "disconnect" ? "Disconnecting…" : "Disconnect"}</button></div>}
    </section>}
  </div>;
}
