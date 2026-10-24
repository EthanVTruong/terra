import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { clockTime, dayLabel, displayNoteText, localDay, timestamp } from "./presentation";
import { deliveryLabel } from "./delivery";
import { farmFeedPollInterval } from "./notification-return";

const isObject = (value) => value && typeof value === "object" && !Array.isArray(value);
const words = (value) => String(value || "Unavailable").replaceAll("_", " ");
function validFeed(value, id) {
  if (!isObject(value) || value.schema_version !== "terra-farm-feed-v1" || value.orchard_id !== id || !Array.isArray(value.messages) || !isObject(value.monitor)) throw new Error("Monitoring updates could not be read. Previous notes are retained.");
  const ids = new Set();
  for (const message of value.messages) {
    if (!isObject(message) || typeof message.id !== "string" || message.orchard_id !== id || ids.has(message.id) || typeof message.text !== "string" || !Number.isFinite(Date.parse(message.created_at)) || !["proactive", "answer"].includes(message.kind)) throw new Error("A monitoring note could not be read. Previous notes are retained.");
    ids.add(message.id);
  }
  return value;
}

export function useFarmFeed(active, { pollInBackground = false, replayOnly = false } = {}) {
  const pollInterval = farmFeedPollInterval(replayOnly);
  const [orchards, setOrchards] = useState([]), [orchardId, setOrchardId] = useState("");
  const [available, setAvailable] = useState(false), [feed, setFeed] = useState(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [revision, setRevision] = useState(0), [profilesRevision, setProfilesRevision] = useState(0);
  const feedRef = useRef(null), selectedIdRef = useRef("");
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/orchards", { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("Monitoring is unavailable.");
      const value = await response.json();
      if (!Array.isArray(value.orchards) || value.orchards.some((item) => !isObject(item) || typeof item.id !== "string" || typeof item.name !== "string")) throw new Error("Monitoring locations could not be read.");
      if (!controller.signal.aborted) { setAvailable(true); setOrchards(value.orchards); setOrchardId((id) => { const next = value.orchards.some((item) => item.id === id) ? id : value.orchards[0]?.id || ""; selectedIdRef.current = next; return next; }); }
    }).catch(() => { if (!controller.signal.aborted) setAvailable(false); });
    return () => controller.abort();
  }, [profilesRevision]);
  useEffect(() => {
    if (!active || !orchardId) return;
    let controller, disposed = false, version = 0;
    async function load() {
      const current = ++version;
      controller?.abort(); controller = new AbortController();
      setLoading(!feedRef.current); setError("");
      try {
        const response = await fetch(`/api/orchards/${encodeURIComponent(orchardId)}/messages`, { signal: controller.signal });
        if (!response.ok) throw new Error("Monitoring updates are unavailable. Previous notes are retained.");
        const value = validFeed(await response.json(), orchardId);
        if (!disposed && current === version && selectedIdRef.current === orchardId) { feedRef.current = value; setFeed(value); }
      } catch (reason) { if (!disposed && current === version && selectedIdRef.current === orchardId && reason.name !== "AbortError") setError(reason.message); }
      finally { if (!disposed && current === version && selectedIdRef.current === orchardId) setLoading(false); }
    }
    load();
    const poll = setInterval(() => { if (pollInBackground || document.visibilityState === "visible") load(); }, pollInterval);
    const visible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { disposed = true; controller?.abort(); clearInterval(poll); document.removeEventListener("visibilitychange", visible); };
  }, [active, orchardId, revision, pollInBackground, pollInterval]);
  const selectOrchard = useCallback((id) => { selectedIdRef.current = id; feedRef.current = null; setFeed(null); setError(""); setOrchardId(id); }, []);
  const addOrchard = useCallback((profile) => {
    setOrchards((previous) => [...previous.filter((item) => item.id !== profile.id), profile]);
    setAvailable(true); selectedIdRef.current = profile.id; feedRef.current = null; setFeed(null); setOrchardId(profile.id); setRevision((value) => value + 1);
  }, []);
  return { orchards, orchardId, orchard: orchards.find((item) => item.id === orchardId), available, feed, loading, error, selectOrchard, addOrchard, refresh: () => setRevision((value) => value + 1), refreshProfiles: () => setProfilesRevision((value) => value + 1) };
}

export function FarmNote({ message, orchard, onDetails, selectedMessage, detailsLoading }) {
  return <article className="farm-note">
    <button className={`incoming-bubble farm-message${selectedMessage === message.id ? " selected-note" : ""}`} onClick={(event) => onDetails(event, message)} aria-busy={detailsLoading && selectedMessage === message.id} title="View readings" aria-describedby={"farm-note-text-" + message.id} aria-label={`Open ${message.kind === "answer" ? "answer" : "field note"} from ${timestamp(message.created_at)}`}><span id={"farm-note-text-" + message.id}>{displayNoteText(message, orchard)}</span></button>
    <div className="farm-note-meta"><time dateTime={message.created_at} title={timestamp(message.created_at)} aria-label={timestamp(message.created_at)}>{clockTime(message.created_at)}</time>{message.delivery && !["not_requested", "published"].includes(message.delivery.provider_status || message.delivery.status || "not_requested") && <span>{deliveryLabel(message.delivery)}</span>}</div>
  </article>;
}

export function FarmAvailability({ farm }) {
  const monitor = farm.feed?.monitor;
  const mode = monitor?.orchard?.mode || farm.orchard?.mode;
  return <>
    {farm.error && <div className="load-error" role="status"><p>{farm.error}</p><button onClick={farm.refresh}>Retry updates</button></div>}
    {mode && mode !== "current" ? <div className="availability-status" role="status"><strong>Replay only</strong><span>These saved readings don’t describe current conditions.</span></div> : monitor && (!monitor.comparison_available || monitor.source_error) && <div className="availability-status" role="status"><strong>Weather support unavailable</strong><span>{monitor.data_through ? `Last reading · ${timestamp(monitor.data_through)}` : "Waiting for usable station readings."}</span></div>}
  </>;
}

export function FarmConversation({ farm, onDetails, selectedMessage, detailsLoading, detailsError }) {
  const monitor = farm.feed?.monitor;
  const messages = [...(farm.feed?.messages || [])].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.id.localeCompare(b.id));
  const mode = monitor?.orchard?.mode || farm.orchard?.mode;
  return <div className="farm-conversation">
    {mode && mode !== "current" && <div className="feed-mode">{mode === "injected" ? "Injected replay" : mode === "synthetic" ? "Synthetic data" : "Historical replay"}</div>}
    <FarmAvailability farm={farm} />
    {farm.loading && !farm.feed ? <div className="empty-state" role="status"><span className="typing-indicator" aria-hidden="true"><i /><i /><i /></span><p>Opening field notes…</p></div> : messages.length ? <div className="farm-messages" role="log" aria-label="Orchard field notes" aria-live="polite" aria-relevant="additions text">{messages.map((message, index) => <React.Fragment key={message.id}>{(!index || localDay(message.created_at) !== localDay(messages[index - 1].created_at)) && <div className="day-separator"><time dateTime={localDay(message.created_at)} title={timestamp(message.created_at)}>{dayLabel(message.created_at)}</time></div>}<FarmNote message={message} orchard={monitor?.orchard || farm.orchard} onDetails={onDetails} selectedMessage={selectedMessage} detailsLoading={detailsLoading} /></React.Fragment>)}</div> : !farm.loading && <div className="empty-state"><Icon name="location" size={22} /><h2>No field notes yet</h2><p>Updates appear here when the monitor records a meaningful change.</p></div>}
    {detailsError && <p className="query-error" role="alert">{detailsError}</p>}
  </div>;
}

export function FarmOverview({ farm }) {
  const monitor = farm.feed?.monitor, summary = monitor?.summary;
  return <div className="farm-overview">
    <div className="chill-reading"><small>Estimated chill</small><strong>{Number.isFinite(summary?.hours) ? <>{summary.hours.toLocaleString(undefined, { maximumFractionDigits: 1 })}<span> h</span></> : "Unavailable"}</strong><span>{summary?.status === "offseason" ? "Outside chill season" : summary?.complete ? "32–45°F · since November 1" : summary ? "Incomplete coverage" : "Waiting for readings"}</span></div>
    <dl className="facts"><div><dt>Weather support</dt><dd>{monitor?.comparison_available ? "Usable" : "Unavailable"}</dd></div><div><dt>Coverage</dt><dd>{Number.isFinite(summary?.coverage) ? `${(summary.coverage * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%` : "Unavailable"}</dd></div><div><dt>Historical pace</dt><dd title={summary?.warning || undefined}>{Number.isFinite(summary?.percentile20_hours) ? words(summary.status) : "Unavailable"}</dd></div></dl>
  </div>;
}
