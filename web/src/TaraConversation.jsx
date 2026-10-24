import { sourceFetch as fetch, sourceIdentity } from "./source-fetch";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { clockTime, dayLabel, localDay, publicationTime, timestamp } from "./presentation";
import { bindTaraOperationSource, createTaraOperation, deviceLocationFromPosition, mergeTaraTimeline, readTaraDraft, readTaraStorage, reconcilableTaraRequests, restoreTaraOperation, taraDisplayText, taraError, taraLocationSetupKey, taraNeedsNotificationChoice, taraNotificationEnabledAt, validateTaraResult, validateTaraSnapshot, writeTaraStorage } from "./tara-state";
import "./tara-conversation.css";
import { showTaraNotification } from "./notification-return";

const EMPTY_PRESETS = [];
const storageScope = (orchardId) => `terra.tara.v1.${orchardId || "setup"}`;
const newId = () => `tara-${crypto.randomUUID()}`;

export function useTaraConversation({ orchardId = "", active = true, onFarmCreated, onSelectOrchard } = {}) {
  const scope = storageScope(orchardId);
  const [state, setState] = useState(null), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [draft, updateDraft] = useState(() => readTaraDraft(`${scope}.draft`));
  const [pending, setPending] = useState(() => restoreTaraOperation(readTaraStorage(`${scope}.pending`)));
  const request = useRef({ version: 0, controller: null }), mutation = useRef(null), currentScope = useRef(scope), hasSnapshot = useRef(false);
  const callbacks = useRef({ onFarmCreated, onSelectOrchard });
  const latestRefresh = useRef(null);
  callbacks.current = { onFarmCreated, onSelectOrchard };

  const setDraft = useCallback((value) => {
    updateDraft(value); writeTaraStorage(`${currentScope.current}.draft`, value);
  }, []);

  const refresh = useCallback(async () => {
    if (!active || mutation.current?.status === "sending") return;
    request.current.controller?.abort();
    const task = { version: request.current.version + 1, controller: new AbortController() };
    request.current = task;
    setLoading(!hasSnapshot.current);
    try {
      const response = await fetch(`/api/tara/conversation${orchardId ? `?orchard_id=${encodeURIComponent(orchardId)}` : ""}`, { signal: task.controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error("The conversation is unavailable. Your saved notes and draft are retained.");
      const value = validateTaraSnapshot(await response.json(), orchardId);
      if (request.current.version === task.version && !task.controller.signal.aborted) {
        hasSnapshot.current = true; setState(value); setStale(false); setError("");
        const operation = mutation.current;
        const serverPending = operation && value.pending_requests.some((item) => item.request_id === operation.body.request_id);
        if (operation && operation.status !== "sending" && serverPending) {
          const stillPending = { ...operation, status: "in_progress" };
          mutation.current = stillPending; setPending(stillPending); writeTaraStorage(`${currentScope.current}.pending`, stillPending);
        } else if (operation?.status === "in_progress" && !serverPending) {
          const confirmedFinished = { ...operation, status: "unconfirmed" };
          mutation.current = confirmedFinished; setPending(confirmedFinished); writeTaraStorage(`${currentScope.current}.pending`, confirmedFinished);
        }
      }
    } catch (reason) {
      if (!task.controller.signal.aborted && request.current.version === task.version) { setStale(true); setError(reason instanceof SyntaxError || reason instanceof TypeError ? "The conversation could not be reached. Your draft is retained." : reason.message); }
    } finally { if (request.current.version === task.version) setLoading(false); }
  }, [active, orchardId]);
  latestRefresh.current = refresh;

  useEffect(() => {
    currentScope.current = scope;
    hasSnapshot.current = false; setState(null); setStale(false); setError(""); updateDraft(readTaraDraft(`${scope}.draft`));
    const restored = restoreTaraOperation(readTaraStorage(`${scope}.pending`));
    mutation.current = restored; setPending(restored);
    refresh();
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    const poll = active ? setInterval(visible, 30000) : null;
    document.addEventListener("visibilitychange", visible);
    return () => { request.current.controller?.abort(); request.current.version += 1; clearInterval(poll); document.removeEventListener("visibilitychange", visible); };
  }, [scope, active, refresh]);

  const run = useCallback(async (operation) => {
    if (mutation.current?.status === "sending") return;
    const operationScope = currentScope.current;
    request.current.controller?.abort(); request.current.version += 1;
    const next = { ...operation, status: "sending" };
    setError("");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 60000);
    try {
      Object.assign(next, bindTaraOperationSource(operation, sourceIdentity()), { status: "sending" });
      mutation.current = next; setPending(next); writeTaraStorage(`${operationScope}.pending`, next);
      const guardResponse = await fetch("/api/sms/status", { signal: controller.signal, cache: "no-store" });
      if (!guardResponse.ok) throw new Error("Reconnect to the local Terra service, then retry this request.");
      const guard = await guardResponse.json();
      if (typeof guard.control_token !== "string" || !guard.control_token) throw new Error("Reload the local Terra page to authorize this request.");
      const response = await fetch(next.path, { method: "POST", headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": guard.control_token }, body: JSON.stringify(next.body), signal: controller.signal });
      let body;
      try { body = await response.json(); } catch { throw new Error("The response could not be read. Your draft is retained; retry the same request."); }
      if (!response.ok) throw taraError(body, response.status);
      const value = validateTaraResult(body, next);
      writeTaraStorage(`${operationScope}.pending`, null);
      if (next.kind === "message") writeTaraStorage(`${operationScope}.draft`, "");
      const discardRecoveredDraft = next.kind === "reconcile" && typeof next.recoveredMessage === "string" && readTaraDraft(`${operationScope}.draft`).trim() === next.recoveredMessage;
      if (discardRecoveredDraft) writeTaraStorage(`${operationScope}.draft`, "");
      if (currentScope.current !== operationScope) return;
      mutation.current = null; hasSnapshot.current = true; setPending(null); setState(value.state); setStale(false); setError("");
      if (next.kind === "message") updateDraft("");
      if (discardRecoveredDraft) updateDraft("");
      if (next.body.action === "restart") {
        writeTaraStorage("terra.tara.v1.location", null); writeTaraStorage(`${storageScope("")}.draft`, "");
        callbacks.current.onSelectOrchard?.("");
      }
      if (["location", "device_location"].includes(next.body.action) && value.state.orchard_id) {
        const profile = value.state.orchards.find((item) => item.id === value.state.orchard_id);
        if (profile) callbacks.current.onFarmCreated?.(profile);
      }
      return value;
    } catch (reason) {
      const completedRace = reason.alreadyCompleted && next.kind === "reconcile";
      const changedSetup = reason.setupChanged && next.kind === "setup" && next.body.action === "device_location";
      const original = completedRace && next.originalOperation?.kind !== "reconcile" ? restoreTaraOperation(next.originalOperation) : null;
      let retained = reason.rejected ? null : { ...next, status: reason.inProgress ? "in_progress" : "unconfirmed" };
      if (completedRace) retained = original && original.body.request_id === next.body.target_request_id ? { ...original, status: "result_available" } : null;
      if (changedSetup) retained = null;
      writeTaraStorage(`${operationScope}.pending`, retained);
      if (currentScope.current === operationScope) {
        mutation.current = retained; setPending(retained);
        setError(reason.name === "AbortError" ? "No confirmation has arrived yet. Refresh the status or retry this same request." : reason instanceof TypeError ? "The local service could not be reached. Your draft is retained; retry the same request." : reason.message);
        if (completedRace || changedSetup) await latestRefresh.current?.();
      }
    } finally { clearTimeout(timer); }
  }, []);

  const send = useCallback((replyTo = null) => {
    if (!draft.trim() || draft.length > 1000 || mutation.current || !state || loading) return;
    return run(createTaraOperation("message", { text: draft.trim(), orchard_id: state.orchard_id, reply_to: replyTo }, newId()));
  }, [draft, loading, run, state]);
  const setup = useCallback((action, body = {}) => {
    if (mutation.current || !state || loading) return;
    return run(createTaraOperation("setup", { action, ...body, ...(action === "notifications" ? { orchard_id: state.orchard_id } : {}) }, newId()));
  }, [loading, run, state]);
  const reconcile = useCallback((targetRequestId) => {
    if (!state || loading || mutation.current?.status === "sending" || !reconcilableTaraRequests(state).some((item) => item.request_id === targetRequestId)) return;
    if (mutation.current && mutation.current.body.request_id !== targetRequestId) return;
    const operation = createTaraOperation("reconcile", { target_request_id: targetRequestId, confirm_uncertain: true, orchard_id: state.orchard_id }, newId());
    if (mutation.current) operation.originalOperation = { ...mutation.current };
    if (mutation.current?.kind === "message") operation.recoveredMessage = mutation.current.body.text;
    return run(operation);
  }, [loading, run, state]);
  const retry = useCallback(() => {
    const previous = mutation.current;
    if (!previous || previous.status === "sending" || previous.status === "in_progress") return;
    return run(previous);
  }, [run]);
  const locked = loading || Boolean(pending) || Boolean(state?.pending_requests.length);
  return { state, selectionScope: scope, loading, stale, error, draft, setDraft, pending, locked, send, setup, reconcile, retry, refresh };
}

function FarmLocation({ conversation }) {
  const [phase, setPhase] = useState("idle"), [error, setError] = useState("");
  const alive = useRef(true), locating = useRef(false), latestConversation = useRef(conversation);
  latestConversation.current = conversation;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  function useDevice() {
    if (locating.current || conversation.locked) return;
    const setupKey = taraLocationSetupKey(conversation.state);
    if (!setupKey) return;
    const selectionScope = conversation.selectionScope;
    setError("");
    let handled = false;
    const current = () => alive.current && latestConversation.current.selectionScope === selectionScope && taraLocationSetupKey(latestConversation.current.state) === setupKey;
    const finish = () => { locating.current = false; if (alive.current) setPhase("idle"); };
    try {
      if (!navigator.geolocation) { setError("This browser can’t share location. Use a browser with location access."); return; }
      locating.current = true; setPhase("locating");
      navigator.geolocation.getCurrentPosition(async (position) => {
        if (handled) return;
        handled = true;
        if (!current()) { finish(); return; }
        try {
          const deviceLocation = deviceLocationFromPosition(position);
          if (latestConversation.current.locked) { setError("Finish the current request, then share your location again."); return; }
          setPhase("saving");
          await latestConversation.current.setup("device_location", { device_location: deviceLocation });
        } catch (reason) { if (alive.current) setError(reason.message); }
        finally { finish(); }
      }, (reason) => {
        if (handled) return;
        handled = true;
        if (current()) setError(reason.code === 1 ? "Location wasn’t shared. Allow location in your browser settings, then try again." : reason.code === 3 ? "Location took too long. Try again." : "Your location couldn’t be found. Try again.");
        finish();
      }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
    } catch { handled = true; finish(); if (alive.current) setError("Location access is unavailable. Check your browser settings, then try again."); }
  }
  return <div className="tara-setup-controls tara-location-request">
    <button type="button" className="tara-location-button" onClick={useDevice} disabled={conversation.locked || phase !== "idle"} aria-busy={phase !== "idle"}><Icon name="location" size={16} /><span>{phase === "locating" ? "Finding location…" : phase === "saving" ? "Saving location…" : "Use my location"}</span></button>
    {error ? <p className="query-error" role="status">{error}</p> : null}
  </div>;
}

function NotificationChoice({ conversation }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const permissionBusy = useRef(false), alive = useRef(true), latestConversation = useRef(conversation);
  latestConversation.current = conversation;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const supported = typeof window.Notification === "function" && conversation.state.notifications.capability === "browser_open";
  async function choose(enabled) {
    if (permissionBusy.current || conversation.locked) return;
    const orchardId = conversation.state.orchard_id;
    permissionBusy.current = true; setBusy(true); setError("");
    try {
      let permission = supported ? Notification.permission : "unsupported";
      if (enabled && supported && permission === "default") permission = await Notification.requestPermission();
      if (!alive.current || latestConversation.current.state?.orchard_id !== orchardId) return;
      await latestConversation.current.setup("notifications", { notifications: { enabled: Boolean(enabled && permission === "granted"), permission } });
    } catch { if (alive.current) setError("Browser alerts couldn’t be enabled. You can keep updates in chat."); }
    finally { permissionBusy.current = false; if (alive.current) setBusy(false); }
  }
  return <div className="tara-setup-controls"><p className="tara-capability">Browser alerts work while Terra is open. Updates always stay in chat.</p><div className="tara-actions"><button className="query-submit" type="button" onClick={() => choose(true)} disabled={!supported || conversation.locked || busy}>{busy ? "Saving choice…" : "Enable browser alerts"}<Icon name="phone" size={14} /></button><button className="query-cancel" type="button" onClick={() => choose(false)} disabled={conversation.locked || busy}>Keep in chat</button></div>{!supported ? <p className="tara-capability">This browser doesn’t support alerts. In-chat updates are available.</p> : null}{error ? <p className="query-error" role="alert">{error}</p> : null}</div>;
}

export function useTaraBrowserAlerts(conversation, farm, { enabled = true, onOpen } = {}) {
  const baseline = useRef({ orchardId: null, seen: new Set(), enabled: false, enabledAt: null });
  const openNote = useRef(onOpen);
  openNote.current = onOpen;
  const [error, setError] = useState("");
  const orchardId = conversation.state?.orchard_id;
  const enabledAt = taraNotificationEnabledAt(conversation.state);
  const effectiveEnabled = Boolean(enabled && enabledAt !== null);
  useEffect(() => {
    const feed = farm?.feed;
    if (!orchardId || feed?.orchard_id !== orchardId) return;
    const notes = (feed.messages || []).filter((note) => note.kind === "proactive");
    if (!effectiveEnabled || baseline.current.orchardId !== orchardId || !baseline.current.enabled || baseline.current.enabledAt !== enabledAt) { baseline.current = { orchardId, seen: new Set(notes.map((note) => note.id)), enabled: effectiveEnabled, enabledAt }; setError(""); return; }
    const additions = notes.filter((note) => !baseline.current.seen.has(note.id) && Date.parse(publicationTime(note)) > enabledAt);
    for (const note of notes) baseline.current.seen.add(note.id);
    if (!conversation.state.notifications.enabled || conversation.state.notifications.capability !== "browser_open" || typeof window.Notification !== "function" || Notification.permission !== "granted") return;
    const profile = conversation.state.orchards.find((item) => item.id === orchardId);
    const farmName = profile?.name || farm?.orchard?.name || "Terra update";
    const title = profile?.mode === "replay" ? `Demo replay · ${farmName}` : profile?.mode === "injected" ? `Injected demo · ${farmName}` : profile?.demo_location ? `Demo farm · ${farmName}` : farmName;
    for (const note of additions) {
      try { showTaraNotification(Notification, title, note, { orchardId, onOpen: (target) => openNote.current?.(target) }); }
      catch { setError("Browser alerts are unavailable here. Your updates remain in chat."); }
    }
  }, [conversation.state?.notifications.enabled, conversation.state?.notifications.capability, conversation.state?.orchards, effectiveEnabled, enabledAt, farm?.feed, farm?.orchard?.name, orchardId]);
  return error;
}

export function TaraConversation({ conversation, farm, onSelectOrchard, onDetails, fieldNotes = EMPTY_PRESETS, renderFieldNote, alertsManaged = false, notificationError = "" }) {
  const { state, loading } = conversation;
  const internalNotificationError = useTaraBrowserAlerts(conversation, farm, { enabled: !alertsManaged, onOpen: (target) => { onSelectOrchard?.(target.orchard_id); onDetails?.(null, target); } });
  const alertsError = notificationError || internalNotificationError;
  if (!state) return <div className="tara-conversation"><p className="tara-status" role="status">{loading ? "Opening conversation…" : "The conversation is unavailable."}</p>{!loading ? <button className="tara-text-button" onClick={conversation.refresh}>Retry conversation</button> : null}</div>;
  const orchard = state.orchards.find((item) => item.id === state.orchard_id);
  const mode = orchard?.mode;
  const turns = state.turns;
  const timeline = mergeTaraTimeline(turns, typeof renderFieldNote === "function" ? fieldNotes.filter((note) => note && (!note.orchard_id || note.orchard_id === state.orchard_id)) : EMPTY_PRESETS);
  return <div className="tara-conversation">
    <div className="tara-turns" role="log" aria-label="Conversation with Terra" aria-live={loading ? "off" : "polite"} aria-relevant="additions text">
      {turns.length === 0 && state.setup.stage === "crop" && state.initial_prompt ? <article className="tara-turn tara-assistant"><div className="incoming-bubble">{state.initial_prompt}</div></article> : null}
      {timeline.map((entry, index) => {
        const turn = entry.value;
        const published = publicationTime(turn);
        const showDay = index === 0 || localDay(published) !== localDay(publicationTime(timeline[index - 1].value));
        const hasDetails = turn.data?.message_id && onDetails;
        const Bubble = hasDetails ? "button" : "div";
        return <React.Fragment key={entry.key}>
          {showDay && <div className="day-separator"><time dateTime={localDay(published)} title={timestamp(published)}>{dayLabel(published)}</time></div>}
          {entry.type === "note" ? renderFieldNote(turn) : turn.role === "event" ? <div className={`tara-saved-event${turn.kind === "notice" ? " tara-notice-event" : ""}`}><Icon name={turn.kind === "location" ? "location" : turn.kind === "notice" ? "file" : "phone"} size={15} /><span>{turn.text}</span>{turn.kind !== "notice" ? <Icon name="check" size={14} /> : null}</div> : <article className={`tara-turn tara-${turn.role}`}>
            <Bubble type={hasDetails ? "button" : undefined} className={turn.role === "assistant" ? "incoming-bubble tara-bubble" : "tara-outgoing tara-bubble"} title={hasDetails ? "View readings" : undefined} aria-label={hasDetails ? `Open answer from ${timestamp(published)}` : undefined} aria-describedby={hasDetails ? `tara-turn-text-${turn.id}` : undefined} onClick={hasDetails ? (event) => onDetails(event, { id: turn.data.message_id, orchard_id: state.orchard_id, text: turn.text, kind: "answer", created_at: turn.created_at }) : undefined}><span id={`tara-turn-text-${turn.id}`}>{taraDisplayText(turn)}</span>{turn.reactions.length > 0 ? <span className="tara-reaction" role="img" aria-label="Terra reacted with a thumbs up">👍</span> : null}</Bubble>
            <div className="tara-turn-meta"><time dateTime={published} title={timestamp(published)} aria-label={timestamp(published)}>{clockTime(published)}</time>{turn.role === "user" && turn.delivery === "local_saved" ? <span className="tara-read-receipt" role="img" aria-label="Received by Terra" title="Received by Terra"><img src={`${import.meta.env.BASE_URL}brand/terra-peek-receipt.png`} alt="" /></span> : null}</div>
          </article>}
        </React.Fragment>;
      })}

    </div>
    {state.setup.stage === "location" ? <FarmLocation conversation={conversation} /> : taraNeedsNotificationChoice(state) ? <NotificationChoice key={state.orchard_id || "setup"} conversation={conversation} /> : null}
    {state.setup.stage === "complete" && (!mode || mode === "current") ? <div className={`tara-monitoring tara-monitoring-${conversation.stale ? "unavailable" : state.monitoring.status}`} role="status"><Icon name="location" size={14} /><span>{conversation.stale ? "Monitoring status couldn’t be refreshed" : state.monitoring.status === "ready" ? "Monitoring" : state.monitoring.status === "pending" ? "Farm saved · connecting monitoring data" : state.monitoring.status === "unavailable" ? "Farm saved · monitoring unavailable" : "Monitoring not configured"}</span></div> : null}
    {state.notifications.enabled ? <p className="tara-alert-scope">{state.notifications.capability === "browser_open" && typeof window.Notification === "function" && Notification.permission === "granted" ? "Browser alerts for this farm while Terra is open" : "Browser alerts need permission · updates stay in chat"}</p> : null}
    {alertsError ? <p className="query-error" role="status">{alertsError}</p> : null}
  </div>;
}

function PendingReplyChoice({ conversation, request }) {
  const [confirmed, setConfirmed] = useState(false);
  const busy = conversation.pending?.status === "sending" || (conversation.pending && conversation.pending.body.request_id !== request.request_id);
  const warningId = `tara-reconcile-${request.request_id}`;
  return <div className="tara-pending-recovery">
    <p id={warningId}>The earlier request may have used ChatGPT allowance. Continuing won’t retry it.</p>
    <label className="tara-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={Boolean(busy)} /><span>I understand the reply will remain unfinished.</span></label>
    <button type="button" className="tara-text-button" aria-describedby={warningId} disabled={!confirmed || Boolean(busy)} onClick={() => conversation.reconcile(request.request_id)}>Continue without this reply</button>
  </div>;
}

export function TaraComposer({ conversation, replyTo = null, onClearReply, leadingControl = null }) {
  const field = useRef(null), restoreFocus = useRef(false);
  const sending = conversation.pending?.status === "sending";
  const stage = conversation.state?.setup.stage;
  useEffect(() => {
    if (!conversation.pending && restoreFocus.current) {
      if (document.activeElement === document.body) field.current?.focus();
      restoreFocus.current = false;
    }
  }, [conversation.pending]);
  function submit(event) {
    event.preventDefault();
    restoreFocus.current = event.currentTarget.contains(document.activeElement);
    conversation.send(typeof replyTo === "string" ? replyTo : replyTo?.id || null);
  }
  return <div className="tara-compose-area">
    {replyTo ? <div className="tara-reply-context"><span>Replying to the selected note</span><button className="icon-button" type="button" aria-label="Clear reply context" onClick={onClearReply}><Icon name="close" size={13} /></button></div> : null}
    {conversation.error ? <div className="tara-request-error" role="alert"><p>{conversation.error}</p></div> : null}
    {conversation.pending ? <p className="tara-request-status" role="status">{sending ? conversation.pending.body.action === "device_location" ? "Saving location…" : "Sending…" : conversation.pending.status === "in_progress" ? "Request still processing" : conversation.pending.status === "result_available" ? "Earlier reply saved · recover its receipt" : conversation.pending.body.action === "device_location" ? "Location save not confirmed" : "Delivery not confirmed"}</p> : null}
    {!conversation.pending && conversation.state?.pending_requests.length ? <p className="tara-request-status" role="status">An earlier request is unfinished. Refresh its status before continuing.</p> : null}
    {reconcilableTaraRequests(conversation.state).map((request) => <PendingReplyChoice key={request.request_id} conversation={conversation} request={request} />)}
    {conversation.error || (conversation.pending && !sending) || conversation.state?.pending_requests.length ? <div className="tara-actions tara-retry-actions">{conversation.pending && conversation.pending.status !== "in_progress" ? <button type="button" className="tara-text-button" onClick={conversation.retry} disabled={sending}>{conversation.pending.status === "result_available" ? "Recover saved reply" : conversation.pending.kind === "reconcile" ? "Confirm saved continuation" : conversation.pending.body.action === "device_location" ? "Retry location" : "Retry same request"}</button> : null}<button type="button" className="tara-text-button" onClick={conversation.refresh} disabled={sending}>Refresh status</button></div> : null}
    <form className="composer tara-composer" onSubmit={submit} aria-label="Message Terra">{leadingControl}<label className="sr-only" htmlFor="tara-message">Message Terra</label><input ref={field} id="tara-message" type="text" maxLength={1000} value={typeof conversation.draft === "string" ? conversation.draft : ""} onChange={(event) => conversation.setDraft(event.target.value)} placeholder={stage === "crop" ? "What are you growing?" : "Message Terra"} disabled={!conversation.state || Boolean(conversation.pending)} autoComplete="off" /><button type="submit" className="composer-send" aria-label="Send message" disabled={conversation.locked || !conversation.draft.trim()}><Icon name="send" size={15} /></button></form>
  </div>;
}

export default TaraConversation;
