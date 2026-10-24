import { sourceFetch as fetch, sourceIdentity } from "./source-fetch";
import { useCallback, useEffect, useRef, useState } from "react";
import { authorizationUrl, connectionError, connectionPhase, connectionPollDelay, connectionStatus, modelCatalog, writerReady } from "./chatgpt-state";

import { recoverExpiredSource } from "./chatgpt-entry";

const CONFIRMED_ACCOUNTS = "terra-chatgpt-plan-confirmed";
const errorText = (reason, fallback) => reason?.name === "Error" ? reason.message : fallback;
function confirmedAccounts() {
  try {
    const value = JSON.parse(localStorage.getItem(CONFIRMED_ACCOUNTS) || "[]");
    return new Set(Array.isArray(value) ? value.filter((id) => typeof id === "string") : []);
  } catch { return new Set(); }
}

export function useChatGPTConnection() {
  const [status, setStatus] = useState(null), [verified, setVerified] = useState(false);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState("");
  const [error, setError] = useState(""), [catalogError, setCatalogError] = useState("");
  const [catalog, setCatalog] = useState(null), [catalogLoading, setCatalogLoading] = useState(false);
  const [authLink, setAuthLink] = useState(""), [expiredAttempt, setExpiredAttempt] = useState(null);
  const [confirmed, setConfirmed] = useState(confirmedAccounts), [pollTick, setPollTick] = useState(0);
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
        setStatus(value); setVerified(true); setError("");
        if (value.state !== "connecting") { setAuthLink(""); setExpiredAttempt(null); }
      }
      return value;
    } catch (reason) {
      if (alive.current && statusVersion.current === version && reason.name !== "AbortError") { setVerified(false); setError(errorText(reason, "The connection couldn’t be checked. Try again.")); }
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

  const attemptId = status?.attempt?.id || "pending";
  const phase = expiredAttempt === attemptId && status?.state === "connecting" ? "expired" : connectionPhase(status);
  useEffect(() => {
    if (busy || phase !== "connecting") return;
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
  }, [busy, phase, status, attemptId, pollTick, loadStatus]);

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
    loadCatalog().then((value) => {
      if (!canceled && alive.current && catalogVersion.current === version && activeAccount.current === accountId) {
        setCatalog(value);
        // A provider catalog can invalidate a previously saved selection. Close the
        // gate immediately, then reconcile the authoritative writer status.
        if ((selectedModel || "") !== value.selected_model) loadStatus({ quiet: true }).catch(() => {});
      }
    })
      .catch((reason) => { if (!canceled && alive.current && catalogVersion.current === version && activeAccount.current === accountId && reason.name !== "AbortError") setCatalogError("Models could not be loaded. Try again."); })
      .finally(() => { if (!canceled && alive.current && catalogVersion.current === version && activeAccount.current === accountId) setCatalogLoading(false); });
    return () => { canceled = true; };
  }, [hasPlan, accountId, selectedModel, loadCatalog, loadStatus]);

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

  async function returnToTerra() {
    if (busyRef.current || sourceIdentity().kind !== "expired") return;
    busyRef.current = true; statusVersion.current += 1;
    setBusy("return_live"); setError("");
    try {
      await recoverExpiredSource(fetch, sourceIdentity(), lifecycle.current?.signal);
      window.location.reload();
    } catch (reason) {
      if (alive.current && reason.name !== "AbortError") setError(errorText(reason, "Terra couldn’t leave this session. Try again."));
    } finally { busyRef.current = false; if (alive.current) setBusy(""); }
  }

  const account = status?.accounts?.find((item) => item.id === accountId);
  const accounts = status?.accounts || [];
  const model = catalog?.models.some((item) => item.slug === catalog.selected_model) ? catalog.selected_model : "";
  const canManage = Boolean(accountId) && phase !== "connecting";

  useEffect(() => {
    const check = () => { if (document.visibilityState !== "hidden" && !busyRef.current) loadStatus({ quiet: true }).catch(() => {}); };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => { window.removeEventListener("focus", check); document.removeEventListener("visibilitychange", check); };
  }, [loadStatus]);

  return { status, verified, sourceExpired: sourceIdentity().kind === "expired", returnToTerra, ready: verified && writerReady(status, Date.now(), catalog), loading, busy, error, catalog, catalogError, catalogLoading,
    authLink, phase, hasPlan, accountId, account, accounts, model, canManage, confirmed,
    refresh, loadStatus, connect, cancel, chooseModel, confirmPlan, retryModels, act };
}
