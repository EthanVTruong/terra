import { sourceFetch as fetch } from "./source-fetch";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { EvidencePanel } from "./EvidencePanel";
import { dataModeLabel, departure, publicationTime, stationName, temperature, timestamp } from "./presentation";
import { FarmAvailability, FarmNote, FarmOverview, useFarmFeed } from "./FarmFeed";
import { TaraComposer, TaraConversation, useTaraBrowserAlerts, useTaraConversation } from "./TaraConversation";
import { writeTaraStorage } from "./tara-state";
import SmsControls from "./SmsControls";
import ChatGPTConnection from "./ChatGPTConnection";
import SimulationConnection from "./SimulationConnection";
import { usePresence } from "./usePresence";
import { farmNoteDetailsPath, farmNoteTarget } from "./notification-return";
import "./tara-integration.css";

const SCHEMA = "terra-location-assessment-v1";
const ESTIMATE_STATUSES = new Set(["ok", "stale", "insufficient_data"]);
const COMPARISON_STATUSES = new Set(["unassessable", "unavailable", "stale", "assessable", "comparison_only"]);
const DATA_MODES = new Set(["historical_replay", "current", "injected", "synthetic"]);
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const finiteOrNull = (value) => value === null || Number.isFinite(value);
const words = (value) => String(value || "Not supplied").replaceAll("_", " ");
function validateAssessment(value) {
  if (!object(value) || value.schema_version !== SCHEMA) throw new Error(`This file must use ${SCHEMA}.`);
  if (!object(value.query) || !object(value.location_estimate) || !object(value.target_comparison) || !object(value.provenance) || !Array.isArray(value.station_findings)) {
    throw new Error("The assessment is missing its query, estimate, comparison, findings or provenance.");
  }
  if (!DATA_MODES.has(value.data_mode)) throw new Error("The assessment has an unsupported data mode.");
  const { query, location_estimate: estimate, target_comparison: comparison } = value;
  if (!Number.isFinite(query.lat) || Math.abs(query.lat) > 90 || !Number.isFinite(query.lon) || Math.abs(query.lon) > 180 || !Number.isFinite(query.elevation_ft)) {
    throw new Error("The assessment needs valid coordinates and an elevation in feet.");
  }
  if (![query.valid_utc, query.as_of_utc].every((item) => typeof item === "string" && Number.isFinite(Date.parse(item)) && /(?:Z|[+-]\d{2}:\d{2})$/i.test(item))) {
    throw new Error("The assessment needs valid, timezone-aware reading and as-of timestamps.");
  }
  if (!ESTIMATE_STATUSES.has(estimate.status) || estimate.unit !== "degF" || !finiteOrNull(estimate.temperature) || !Array.isArray(estimate.used_station_ids) || !Array.isArray(estimate.excluded)) {
    throw new Error("The location estimate has an unsupported status, value or peer list.");
  }
  if (!estimate.used_station_ids.every(Number.isInteger) || estimate.excluded.some((item) => !object(item) || !Number.isInteger(item.station_id) || typeof item.reason !== "string")) {
    throw new Error("The location estimate has an invalid peer or exclusion entry.");
  }
  if (estimate.status === "insufficient_data" && estimate.temperature !== null) throw new Error("An unavailable estimate must have a null temperature.");
  if (!COMPARISON_STATUSES.has(comparison.status) || ![comparison.observed_f, comparison.expected_f, comparison.departure_f].every(finiteOrNull)) {
    throw new Error("The target comparison has an unsupported status or value.");
  }
  const bounds = [comparison.typical_low_f, comparison.typical_high_f];
  const hasRangeFields = bounds.some((item) => item !== undefined);
  if (hasRangeFields && (!bounds.every(finiteOrNull) || (bounds[0] === null) !== (bounds[1] === null))) {
    throw new Error("The typical range needs two finite bounds or two null values.");
  }
  if (bounds.every(Number.isFinite) && (bounds[0] > bounds[1] || !["assessable", "stale"].includes(comparison.status) || !comparison.reference_id || comparison.expected_method !== "terra-pair-adjusted-median")) {
    throw new Error("A typical range requires an ordered, calibrated station comparison.");
  }
  if (comparison.station_id !== null && !Number.isInteger(comparison.station_id)) throw new Error("The local sensor identifier must be an integer or null.");
  if ((comparison.station_id === null || comparison.station_id === undefined) && [comparison.observed_f, comparison.expected_f, comparison.departure_f].some((item) => item !== null)) {
    throw new Error("A comparison without a local sensor must not contain measured values.");
  }
  if (value.station_findings.some((item) => !object(item) || !Number.isInteger(item.station_id))) throw new Error("Station findings need integer station identifiers.");
  const live = value.provenance.live_source;
  if (live !== undefined && (!object(live) || live.provider !== "CIMIS" || !["refreshed", "cached", "cached_after_error"].includes(live.status) || ![live.missing_station_ids, live.failed_station_ids].every((ids) => Array.isArray(ids) && ids.every((id) => Number.isInteger(id) && id > 0)) || !(live.warning === null || typeof live.warning === "string"))) {
    throw new Error("The current source metadata is invalid. The previous result is retained.");
  }
  return value;
}

function validateIndex(value) {
  if (!object(value) || value.schema_version !== "terra-assessment-index-v1" || !Array.isArray(value.assessments) || !value.assessments.length) throw new Error("The examples index is unavailable or invalid.");
  const ids = new Set();
  for (const item of value.assessments) {
    if (!object(item) || typeof item.id !== "string" || typeof item.title !== "string" || typeof item.path !== "string" || ids.has(item.id)) throw new Error("The examples index has an invalid entry.");
    ids.add(item.id);
  }
  return value.assessments;
}

function validateContext(value) {
  if (!object(value) || value.schema_version !== "terra-live-context-v1" || typeof value.configured !== "boolean" || value.source !== "CIMIS" || !Array.isArray(value.station_presets)) throw new Error("Current weather is unavailable. Saved assessments still work.");
  for (const station of value.station_presets) {
    if (!object(station) || !Number.isInteger(station.station_id) || station.station_id <= 0 || typeof station.name !== "string" || ![station.lat, station.lon, station.elevation_ft].every(Number.isFinite) || Math.abs(station.lat) > 90 || Math.abs(station.lon) > 180) throw new Error("The station locations could not be loaded. Retry the connection.");
  }
  return value;
}

function queryValues(draft) {
  const number = (value, label) => {
    if (typeof value !== "string" || !value.trim() || !Number.isFinite(Number(value))) throw new Error(`Enter a finite number for ${label}.`);
    return Number(value);
  };
  const lat = number(draft.lat, "latitude"), lon = number(draft.lon, "longitude"), elevation_ft = number(draft.elevation, "elevation");
  if (Math.abs(lat) > 90) throw new Error("Latitude must be between −90 and 90.");
  if (Math.abs(lon) > 180) throw new Error("Longitude must be between −180 and 180.");
  return { lat, lon, elevation_ft, ...(draft.preset ? { target_station_id: Number(draft.preset), location_id: `CIMIS-${draft.preset}` } : {}) };
}

function liveError(body, status) {
  const code = body?.error?.code;
  const messages = {
    source_not_configured: "Current weather isn’t connected yet. Saved assessments remain available.",
    provider_auth: "Current weather could not be accessed. Please try again later.",
    provider_unavailable: "The weather source is unavailable. Try again shortly.",
    provider_invalid_response: "The weather source returned unusable data. The previous result is retained.",
    no_hourly_observations: "No current hourly readings were available. Try again shortly.",
    source_geometry_changed: "Station location information changed. Current comparisons need review before another check.",
    invalid_query: "Check the location values and try again.",
  };
  const error = new Error(messages[code] || (status === 422 ? messages.invalid_query : "Current weather could not be checked. Try again."));
  error.retryable = body?.error?.retryable === true;
  return error;
}

function Values({ observed, expected, difference }) {
  return <div className="compact-finding"><div className="message-facts"><span><small>Observed</small><strong>{temperature(observed)}</strong></span><span><small>Expected</small><strong>{temperature(expected)}</strong></span></div><div className="message-departure"><span>Departure</span><strong>{departure(difference)}</strong></div></div>;
}

function AssessmentFacts({ packet, sourceKind, onOpenEvidence }) {
  const { query, location_estimate: estimate, target_comparison: comparison, provenance } = packet;
  const hasTarget = comparison.station_id !== null && comparison.station_id !== undefined;
  const unavailable = estimate.status !== "ok";
  const relevantFindings = hasTarget ? packet.station_findings.filter((finding) => finding.station_id !== comparison.station_id) : packet.station_findings;
  const live = provenance.live_source;
  const sourceWarning = Boolean(live && (live.status === "cached_after_error" || live.warning || live.missing_station_ids?.length || live.failed_station_ids?.length));
  return <>
    <div className="assessment-time"><time dateTime={query.valid_utc}>{timestamp(query.valid_utc)}</time></div>
    <div className="data-mode">{dataModeLabel(packet, sourceKind)}</div>
    <div className="location-context"><span>{query.lat.toFixed(4)}, {query.lon.toFixed(4)}</span><span>{query.elevation_ft.toLocaleString()} ft</span></div>
    {sourceWarning && <div className="availability-status source-warning" role="status"><strong>{live.status === "cached_after_error" ? "Using saved weather after a refresh failure" : "Some station data is unavailable"}</strong><span>{live.status === "cached_after_error" ? "The last successfully retrieved readings are retained. Check their reading time before relying on them." : live.warning || "The assessment uses the source readings that were available."}</span></div>}
    {unavailable && <div className="availability-status" role="status"><strong>{estimate.status === "stale" ? "Estimate is stale" : "Location estimate unavailable"}</strong><span>{words(estimate.status_reason)}</span>{estimate.status === "stale" && <small>The retained value describes the reading time shown above.</small>}</div>}
    <div className="messages">
      {hasTarget && <section className="message-group" aria-label="Measured station comparison">
        <div className="message-meta"><span>{stationName(comparison.station_id)}</span><span>{comparison.classification === "suspected_anomaly" ? "Suspected departure" : "Station comparison"}</span></div>
        {comparison.status !== "assessable" && <p className="comparison-notice">{comparison.status === "comparison_only" ? "Comparison only · no calibrated alert policy" : `${words(comparison.status)} · ${words(comparison.reason)}`}</p>}
        <button className="incoming-bubble reading-button" onClick={(event) => onOpenEvidence(event, null, comparison)} aria-label={`Open ${stationName(comparison.station_id)} temperature comparison`}><Values observed={comparison.observed_f} expected={comparison.expected_f} difference={comparison.departure_f} /></button>
        <p className="message-caption">{comparison.status !== "assessable" ? "No current sensor-fault conclusion is available." : comparison.classification === "suspected_anomaly" ? "Suspected anomaly · check an independent reading." : comparison.classification === "no_alert" ? "No alert triggered." : "No sensor-fault conclusion is available."}</p>
      </section>}
      <section className={`message-group${hasTarget ? " secondary-estimate" : ""}`} aria-label="Location temperature estimate">
        <div className="message-meta"><span>{hasTarget ? "At this location" : "Temperature estimate"}</span></div>
        <div className="incoming-bubble estimate-bubble"><strong className="estimate-value">{temperature(estimate.temperature)}</strong><span className="estimate-support">{estimate.n_used ?? estimate.used_station_ids.length} {estimate.status === "ok" ? "stations · estimated" : "usable stations"}</span></div>
        {!hasTarget && <p className="message-caption">No local sensor reading supplied.</p>}
      </section>
      <section className="message-group station-findings" aria-label={hasTarget ? "Other relevant station flags" : "Relevant station flags"}>

        {relevantFindings.length ? relevantFindings.map((finding, index) => {
          const summary = finding.summary || {};
          const peak = finding.evidence_peak_utc || summary.peak_timestamp || summary.peak_utc;
          const hasValues = [summary.observed_f, summary.expected_f, summary.departure_f].every(Number.isFinite);
          return <article className="station-note" key={`${finding.station_id}:${finding.start_utc || index}`}>
            <div className="station-note-heading"><strong>{stationName(finding.station_id)}</strong><span>{finding.freshness === "stale" ? "Stale finding" : "Suspected departure"}</span></div>
            <button className="incoming-bubble reading-button" onClick={(event) => onOpenEvidence(event, finding)} aria-label={`Open ${stationName(finding.station_id)} temperature evidence`}>{hasValues ? <Values observed={summary.observed_f} expected={summary.expected_f} difference={summary.departure_f} /> : <p className="note-text">{finding.message || "A suspected temperature departure is recorded."}</p>}</button>
            <p className="message-caption">Check an independent reading.{peak && Date.parse(peak) !== Date.parse(query.valid_utc) ? ` Evidence · ${timestamp(peak)}` : ""}{finding.freshness === "stale" ? " · stale" : ""}</p>
          </article>;
        }) : <p className="no-findings">{hasTarget ? "No other station flags." : "No station flags."}</p>}
      </section>
    </div>
  </>;
}

export function App() {
  const [view, setView] = useState("monitor");
  const [taraFarmId, setTaraFarmId] = useState(() => {
    try { const value = localStorage.getItem("terra.tara.activeFarm"); return value && /^[A-Za-z0-9_-]{1,64}$/.test(value) ? value : ""; }
    catch { return ""; }
  });
  const [notificationNote, setNotificationNote] = useState(null);
  const [sourceChanging, setSourceChanging] = useState(false);
  const [connectionRequest, setConnectionRequest] = useState(0);
  const farmRef = useRef(null);
  const chooseTaraFarm = useCallback((id) => {
    setNotificationNote(null);
    setTaraFarmId(id);
    try { if (id) localStorage.setItem("terra.tara.activeFarm", id); else localStorage.removeItem("terra.tara.activeFarm"); } catch { /* This tab can retain the selection in memory. */ }
    farmRef.current?.selectOrchard(id);
  }, []);
  const addTaraFarm = useCallback((profile) => { farmRef.current?.addOrchard(profile); farmRef.current?.refreshProfiles(); chooseTaraFarm(profile.id); }, [chooseTaraFarm]);
  const nativeTara = useTaraConversation({ orchardId: taraFarmId, active: true, onFarmCreated: addTaraFarm, onSelectOrchard: chooseTaraFarm });
  const tara = { ...nativeTara, locked: nativeTara.locked || sourceChanging };
  const browserAlerts = Boolean(tara.state?.notifications.enabled && tara.state.notifications.capability === "browser_open");
  const [smsStatus, setSmsStatus] = useState(null);
  const [farmDetails, setFarmDetails] = useState(null);
  const [selectedMessage, setSelectedMessage] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false), [detailsError, setDetailsError] = useState("");
  const [context, setContext] = useState(null);
  const farm = useFarmFeed(view === "monitor" || browserAlerts, { pollInBackground: browserAlerts, replayOnly: context?.replay_only === true });
  farmRef.current = farm;
  const browserAlertError = useTaraBrowserAlerts(tara, farm, { onOpen: openNotificationNote });
  const detailRequest = useRef({ version: 0, controller: null }), nearBottom = useRef(true);
  const [entries, setEntries] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [packet, setPacket] = useState(null);
  const [resultSource, setResultSource] = useState(null);
  const [error, setError] = useState("");
  const [retryable, setRetryable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [pendingKind, setPendingKind] = useState(null);
  const [notice, setNotice] = useState("");
  const [indexRetry, setIndexRetry] = useState(0);
  const [contextError, setContextError] = useState("");
  const [contextLoading, setContextLoading] = useState(true);
  const [contextRetry, setContextRetry] = useState(0);
  const [queryOpen, setQueryOpen] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelTab, setPanelTab] = useState("temperature");
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const [draft, setDraft] = useState({ lat: "", lon: "", elevation: "", preset: "" });
  const [queryError, setQueryError] = useState("");
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 700px)").matches);
  const [mobileJson, setMobileJson] = useState(false);
  const fileInput = useRef(null), closeButton = useRef(null), jsonPre = useRef(null), opener = useRef(null);
  const queryTrigger = useRef(null), queryForm = useRef(null), latitudeInput = useRef(null);
  const controlsTrigger = useRef(null), controlsForm = useRef(null), panelTrigger = useRef(null), messageScroll = useRef(null);
  const userSelection = useRef(true), draftEdited = useRef(false), lastAttempt = useRef(null);
  const request = useRef({ version: 0, controller: null });
  const indexUrl = new URL(`${import.meta.env.BASE_URL}assessments/index.json`, window.location.origin).href;

  const begin = useCallback((kind) => {
    request.current.controller?.abort();
    const task = { version: request.current.version + 1, controller: new AbortController() };
    request.current = task;
    setLoading(true); setPendingKind(kind); setError(""); setNotice(""); setRetryable(true);
    return task;
  }, []);
  const active = useCallback((task) => request.current.version === task.version && !task.controller.signal.aborted, []);
  const finish = useCallback((task) => {
    if (active(task)) { setLoading(false); setPendingKind(null); request.current.controller = null; }
  }, [active]);
  const fail = useCallback((task, reason) => {
    if (active(task)) { setError(reason.message); setRetryable(reason.retryable !== false); }
  }, [active]);

  const loadExample = useCallback(async (entry) => {
    const task = begin("example");
    lastAttempt.current = { kind: "example", entry };
    setSelectedId(entry.id);
    try {
      let url;
      try { url = new URL(entry.path, indexUrl); }
      catch { throw new Error("The example path is invalid."); }
      if (url.origin !== window.location.origin || !url.pathname.startsWith(new URL("./", indexUrl).pathname)) throw new Error("The example path is outside the local assessments directory.");
      const response = await fetch(url, { signal: task.controller.signal });
      if (!response.ok) throw new Error("This example could not be loaded. Retry or choose another assessment.");
      const value = validateAssessment(await response.json());
      if (active(task)) { setPacket(value); setResultSource({ kind: "example", label: entry.title, description: entry.description }); }
    } catch (reason) { fail(task, reason); }
    finally { finish(task); }
  }, [active, begin, fail, finish, indexUrl]);

  const loadQuery = useCallback(async (values) => {
    const startedInForm = queryForm.current?.contains(document.activeElement);
    const task = begin("query");
    lastAttempt.current = { kind: "query", values: { ...values } };
    setSelectedId("");
    try {
      const response = await fetch("/api/assess", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values), signal: task.controller.signal });
      let body;
      try { body = await response.json(); }
      catch { throw new Error("The weather service returned an unreadable response. The previous result is retained."); }
      if (!response.ok) throw liveError(body, response.status);
      const value = validateAssessment(body);
      if (value.data_mode !== "current") throw new Error("The weather service did not return a current assessment. The previous result is retained.");
      if (active(task)) {
        const returnFocus = startedInForm && queryForm.current?.dataset.open === "true" && (document.activeElement === document.body || queryForm.current.contains(document.activeElement));
        setView("assessment"); setPacket(value); setResultSource({ kind: "query", label: "Current weather query" }); setQueryOpen(false);
        if (returnFocus) requestAnimationFrame(() => queryTrigger.current?.focus());
      }
    } catch (reason) {
      if (reason instanceof TypeError) reason = new Error("Current weather could not be reached. Retry or use a saved assessment.");
      fail(task, reason);
    } finally { finish(task); }
  }, [active, begin, fail, finish]);

  const loadFile = useCallback(async (file) => {
    const task = begin("file");
    lastAttempt.current = { kind: "file", file };
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error("Choose an assessment JSON smaller than 10 MB.");
      const value = validateAssessment(JSON.parse(await file.text()));
      if (active(task)) { setView("assessment"); setSelectedId(""); setPacket(value); setResultSource({ kind: "file", label: file.name }); setMobileJson(false); }
    } catch (reason) { fail(task, reason instanceof SyntaxError ? new Error("This file is not valid JSON. The previous result is retained.") : reason); }
    finally { finish(task); }
  }, [active, begin, fail, finish]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 700px)");
    const change = () => { setMobile(media.matches); setMobileJson(false); };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => () => { request.current.controller?.abort(); request.current.version += 1; }, []);

  useEffect(() => {
    const controller = new AbortController();
    if (!userSelection.current) { setLoading(true); setError(""); }
    fetch(indexUrl, { signal: controller.signal }).then((response) => {
      if (!response.ok) throw new Error("Example assessments are unavailable. Retry or open a local assessment JSON.");
      return response.json();
    }).then((value) => {
      const next = validateIndex(value);
      if (controller.signal.aborted) return;
      setEntries(next);
      if (!userSelection.current) loadExample(next[0]);
    }).catch((reason) => {
      if (!controller.signal.aborted && !userSelection.current) { setError(reason.message); setLoading(false); setPendingKind(null); }
    });
    return () => controller.abort();
  }, [indexUrl, indexRetry, loadExample]);

  useEffect(() => {
    const controller = new AbortController();
    setContextLoading(true); setContextError("");
    fetch("/api/context", { signal: controller.signal }).then((response) => {
      if (!response.ok) throw new Error("Current weather is unavailable. Saved assessments still work.");
      return response.json();
    }).then((value) => { if (!controller.signal.aborted) setContext(validateContext(value)); })
      .catch((reason) => { if (!controller.signal.aborted) { setContext(null); setContextError(reason instanceof SyntaxError || reason instanceof TypeError ? "Current weather is unavailable. Saved assessments still work." : reason.message); } })
      .finally(() => { if (!controller.signal.aborted) setContextLoading(false); });
    return () => controller.abort();
  }, [contextRetry]);

  useEffect(() => { if (mobileJson) closeButton.current?.focus(); }, [mobileJson]);
  useEffect(() => {
    if (tara.state && (tara.state.orchard_id || "") !== farm.orchardId) farm.selectOrchard(tara.state.orchard_id || "");
  }, [tara.state?.orchard_id, farm.orchardId, farm.selectOrchard]);
  useEffect(() => {
    if (farm.available && tara.state && taraFarmId && !farm.orchards.some((item) => item.id === taraFarmId) && !tara.state.orchards.some((item) => item.id === taraFarmId)) chooseTaraFarm("");
  }, [farm.available, farm.orchards, tara.state, taraFarmId, chooseTaraFarm]);
  useEffect(() => {
    detailRequest.current.controller?.abort(); detailRequest.current.version += 1;
    setFarmDetails(null); setSelectedMessage(null); setDetailsLoading(false); setDetailsError("");
  }, [farm.orchardId]);
  useEffect(() => {
    // Switching farms clears old evidence; wait for both native views to agree.
    if (!notificationNote || view !== "monitor" || notificationNote.orchard_id !== farm.orchardId || notificationNote.orchard_id !== tara.state?.orchard_id) return;
    setNotificationNote(null);
    openFarmDetails(null, notificationNote);
  }, [notificationNote, view, farm.orchardId, tara.state?.orchard_id]);
  useEffect(() => () => detailRequest.current.controller?.abort(), []);
  useEffect(() => {
    if (view === "monitor" && nearBottom.current) requestAnimationFrame(() => {
      const container = messageScroll.current;
      container?.scrollTo({ top: container.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  }, [farm.feed?.messages, tara.state?.turns, view]);
  useEffect(() => { if (queryOpen) latitudeInput.current?.focus(); }, [queryOpen]);
  useEffect(() => { if (controlsOpen) controlsForm.current?.querySelector(".query-heading button")?.focus(); }, [controlsOpen]);
  useEffect(() => { setSelectedEvidence(null); messageScroll.current?.scrollTo(0, 0); }, [packet]);
  useEffect(() => {
    if (!queryOpen && !controlsOpen) return;
    function dismiss(event) {
      if (sourceChanging) return;
      if (queryForm.current?.contains(event.target) || queryTrigger.current?.contains(event.target) || controlsForm.current?.contains(event.target) || controlsTrigger.current?.contains(event.target)) return;
      setQueryOpen(false); setControlsOpen(false);
    }
    function escape(event) {
      if (sourceChanging) return;
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (queryOpen) { setQueryOpen(false); (queryTrigger.current?.isConnected ? queryTrigger.current : controlsTrigger.current)?.focus(); }
      else { setControlsOpen(false); controlsTrigger.current?.focus(); }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [queryOpen, controlsOpen, sourceChanging]);

  function showEvidence(event, finding = null, comparison = null) {
    const matching = comparison && packet.station_findings.find((item) => item.station_id === comparison.station_id && Array.isArray(item.evidence) && item.evidence.some((row) => Date.parse(row?.timestamp) === Date.parse(packet.query.valid_utc)));
    opener.current = event.currentTarget; setPanelTab("temperature"); setSelectedEvidence({ finding: finding || matching || null, comparison });
    if (mobile) setMobileJson(true); else setPanelOpen(true);
  }
  function closeJson() { setMobileJson(false); if (!mobile) setPanelOpen(false); requestAnimationFrame(() => (opener.current?.isConnected ? opener.current : panelTrigger.current)?.focus()); }
  function retry() {
    userSelection.current = true;
    const attempt = lastAttempt.current;
    if (attempt?.kind === "query") loadQuery({ ...attempt.values, refresh: true });
    else if (attempt?.kind === "example") loadExample(attempt.entry);
    else if (attempt?.kind === "file") loadFile(attempt.file);
    else { userSelection.current = false; setIndexRetry((value) => value + 1); }
  }
  function cancel() {
    request.current.controller?.abort(); request.current.version += 1;
    setLoading(false); setPendingKind(null); setError("");
    setNotice(packet ? "Request cancelled. The last successful result is retained." : "Request cancelled.");
  }
  function selectExample(event) {
    const entry = entries.find((item) => item.id === event.target.value);
    if (!entry) return;
    userSelection.current = true; setView("assessment"); setMobileJson(false); setQueryOpen(false); setControlsOpen(false); loadExample(entry);
    requestAnimationFrame(() => controlsTrigger.current?.focus());
  }
  function openFile(event) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    userSelection.current = true; setQueryOpen(false); setControlsOpen(false); loadFile(file);
    requestAnimationFrame(() => controlsTrigger.current?.focus());
  }
  function toggleQuery() {
    setControlsOpen(false);
    if (!queryOpen && !draftEdited.current && packet) {
      const preset = context?.station_presets.find((station) => station.station_id === packet.query.target_station_id);
      setDraft({ lat: String(packet.query.lat), lon: String(packet.query.lon), elevation: String(packet.query.elevation_ft), preset: preset ? String(preset.station_id) : "" });
    }
    setQueryError(""); setQueryOpen((value) => !value);
  }
  function closeQuery() { setQueryOpen(false); requestAnimationFrame(() => (queryTrigger.current?.isConnected ? queryTrigger.current : controlsTrigger.current)?.focus()); }
  function editField(field, value) {
    draftEdited.current = true; setQueryError(""); setDraft((previous) => ({ ...previous, [field]: value, preset: "" }));
  }
  function choosePreset(event) {
    const preset = context?.station_presets.find((station) => String(station.station_id) === event.target.value);
    draftEdited.current = true; setQueryError("");
    setDraft((previous) => preset ? { lat: String(preset.lat), lon: String(preset.lon), elevation: String(preset.elevation_ft), preset: String(preset.station_id) } : { ...previous, preset: "" });
  }
  function submitQuery(event) {
    event.preventDefault();
    try {
      const values = queryValues(draft);
      if (!context?.configured) throw new Error("Current weather isn’t connected yet. Saved assessments remain available.");
      userSelection.current = true; setQueryError(""); loadQuery(values);
    } catch (reason) { setQueryError(reason.message); }
  }

  function openMonitor() {
    userSelection.current = true;
    request.current.controller?.abort(); request.current.version += 1;
    setLoading(false); setPendingKind(null); setError(""); setNotice(""); setView("monitor"); setQueryOpen(false); setControlsOpen(false);
    setPanelTab("temperature"); setMobileJson(false); nearBottom.current = true;
  }

  async function startTaraFromLocation() {
    try {
      const values = queryValues(draft);
      if (!tara.state || tara.locked) throw new Error("Finish the current conversation request first.");
      if (tara.state.setup.stage === "notifications") throw new Error("Finish choosing notifications before adding another farm.");
      if (tara.state.setup.stage === "complete" && !(await tara.setup("restart"))) return;
      writeTaraStorage("terra.tara.v1.location", { name: "", lat: String(values.lat), lon: String(values.lon), elevation: String(values.elevation_ft), source: draft.preset ? "preset" : "manual", confirmed: false });
      chooseTaraFarm(""); openMonitor();
    } catch (reason) { setQueryError(reason.message); }
  }

  function openNotificationNote(message) {
    const target = farmNoteTarget(message);
    if (target.orchard_id !== taraFarmId) chooseTaraFarm(target.orchard_id);
    openMonitor();
    setNotificationNote(target);
  }

  async function openFarmDetails(event, message) {
    let target;
    try { target = farmNoteTarget(message, tara.state?.orchard_id || farm.orchardId); }
    catch (reason) { setDetailsError(reason.message); return; }
    if (target.orchard_id !== (tara.state?.orchard_id || farm.orchardId)) {
      openNotificationNote(target);
      return;
    }
    opener.current = event?.currentTarget || panelTrigger.current; setPanelTab("temperature");
    if (mobile) setMobileJson(true); else setPanelOpen(true);
    if (detailsLoading && selectedMessage === message.id) return;
    detailRequest.current.controller?.abort();
    const task = { version: detailRequest.current.version + 1, controller: new AbortController() };
    detailRequest.current = task;
    setSelectedMessage(message.id); setFarmDetails(null); setDetailsLoading(true); setDetailsError("");
    try {
      const detailFarmId = target.orchard_id;
      const response = await fetch(farmNoteDetailsPath(target), { signal: task.controller.signal });
      if (!response.ok) throw new Error("The note’s readings are unavailable. Tap the note to retry.");
      const value = await response.json();
      if (value.message?.id !== message.id || value.message?.orchard_id !== detailFarmId || !Array.isArray(value.assessments) || !Array.isArray(value.events)) throw new Error("The note’s evidence could not be read.");
      if (detailRequest.current.version === task.version && !task.controller.signal.aborted) setFarmDetails(value);
    } catch (reason) { if (detailRequest.current.version === task.version && !task.controller.signal.aborted) setDetailsError(reason.message); }
    finally { if (detailRequest.current.version === task.version) setDetailsLoading(false); }
  }

  const showSmsAssessment = useCallback((value) => {
    try {
      const checked = validateAssessment(value);
      request.current.controller?.abort(); request.current.version += 1;
      userSelection.current = true; setView("assessment"); setLoading(false); setPendingKind(null); setError("");
      setPacket(checked); setSelectedId(""); setQueryOpen(false); setControlsOpen(false); setMobileJson(false);
      setResultSource({ kind: "demo", label: "Simulated flag", description: "SMS demo · simulated observations" });
    } catch { setNotice("A demo response was received, but its assessment could not be displayed."); }
  }, []);

  const currentFarmDetails = farmDetails?.message?.orchard_id === tara.state?.orchard_id ? farmDetails : null;
  const matchingFarm = Boolean(tara.state?.orchard_id && farm.orchardId === tara.state.orchard_id && farm.feed?.orchard_id === tara.state.orchard_id);
  const farmPackets = (currentFarmDetails?.assessments || []).flatMap((item) => {
    try { return [validateAssessment(item)]; } catch { return []; }
  }).sort((a, b) => Date.parse(a.query.valid_utc) - Date.parse(b.query.valid_utc));
  const panelPacket = view === "monitor" ? farmPackets.at(-1) || null : packet;
  const panelRecord = view === "monitor" ? currentFarmDetails || (matchingFarm ? farm.feed : null) : null;
  const panelReady = view === "monitor" ? matchingFarm : Boolean(packet);
  const primaryLabel = view === "monitor" && !panelPacket ? "Chill" : "Temperature";
  const monitorOverview = detailsLoading ? <div className="empty-state" role="status"><span className="typing-indicator" aria-hidden="true"><i /><i /><i /></span><p>Opening evidence…</p></div> : panelPacket ? <details className="disclosure farm-extra"><summary><Icon name="file" /><span>Chill</span><Icon name="chevron" size={12} /></summary><div className="disclosure-body"><FarmOverview farm={farm} /></div></details> : <FarmOverview farm={farm} />;
  const showPanel = panelReady && (mobile ? mobileJson : panelOpen);
  const panelMounted = usePresence(showPanel, 220);
  const queryMounted = usePresence(queryOpen, 180);
  const controlsMounted = usePresence(controlsOpen, 180);
  const evidenceFinding = selectedEvidence?.finding || (!selectedEvidence?.comparison ? packet?.station_findings[0] : null);
  const evidenceComparison = selectedEvidence?.comparison || (!evidenceFinding && packet?.target_comparison.station_id !== null ? packet?.target_comparison : null);
  const querying = loading && pendingKind === "query";
  const previewNote = resultSource?.kind === "query" ? "Provider query · checked on demand" : resultSource?.kind === "file" ? `Local file · ${resultSource.label}` : resultSource?.description || "Saved assessment · no message delivery";
  const fieldNotes = matchingFarm ? farm.feed.messages : [];
  const latestReplyId = tara.state?.turns.filter((turn) => turn.role === "assistant").at(-1)?.id || null;
  return <main className={`app-window${showPanel ? " with-artifacts" : ""}${panelMounted ? " panel-mounted" : ""}`}>
    <section className="conversation" inert={mobile && mobileJson ? true : undefined} aria-hidden={mobile && mobileJson ? true : undefined}>
      <header className="thread-header"><h1 className="brand-character"><img src={`${import.meta.env.BASE_URL}brand/terra-cube-1080.png`} alt="terra" /></h1><div className="thread-actions">{(context?.replay_only === true || querying || (view === "monitor" && tara.state?.setup.stage === "complete" && tara.state?.monitoring.status === "unavailable")) && <span className="presence" role="status">{context?.replay_only === true ? "Replay" : querying ? "Checking…" : "Updates unavailable"}</span>}<ChatGPTConnection openRequest={connectionRequest} refreshRequest={latestReplyId} showTrigger={false} returnFocusRef={controlsTrigger} /><SmsControls onAssessment={showSmsAssessment} onStatus={setSmsStatus} orchard={farm.orchard} /><button ref={panelTrigger} className={`icon-button${showPanel ? " selected" : ""}`} onClick={(event) => { opener.current = event.currentTarget; if (showPanel) closeJson(); else { if (mobile) setMobileJson(true); else setPanelOpen(true); } }} aria-label={showPanel ? "Hide evidence panel" : "Show evidence panel"} aria-expanded={showPanel}><Icon name="panel" size={17} /></button></div></header>
      <div ref={messageScroll} className="message-scroll" aria-busy={view === "monitor" ? farm.loading : loading} onScroll={(event) => { const node = event.currentTarget; nearBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48; }}>
        {view === "monitor" ? <>
          {notice && <p className="request-notice" role="status">{notice}</p>}
          {matchingFarm && <FarmAvailability farm={farm} />}
          <TaraConversation conversation={tara} showFarmSelector={false} stationPresets={context?.station_presets || []} farm={farm} onSelectOrchard={(id) => { chooseTaraFarm(id); openMonitor(); }} onDetails={openFarmDetails} onOpenConnection={() => setConnectionRequest((value) => value + 1)} fieldNotes={fieldNotes} renderFieldNote={(message) => <FarmNote message={message} orchard={farm.orchard} onDetails={openFarmDetails} selectedMessage={selectedMessage} detailsLoading={detailsLoading} />} alertsManaged notificationError={browserAlertError} />
          {detailsError && <p className="query-error" role="alert">{detailsError}</p>}
        </> : <>
        {error && <div className="load-error" role="alert"><p>{error}</p>{retryable && <button onClick={retry}><Icon name="refresh" size={14} />{lastAttempt.current?.kind === "query" ? "Retry query" : "Retry"}</button>}</div>}
        {loading && packet && <div className="request-status" role="status"><span>{querying ? "Checking location…" : "Opening assessment…"}<small>Previous result shown below.</small></span>{!queryOpen && <button onClick={cancel}>Cancel</button>}</div>}
        {notice && <p className="request-notice" role="status">{notice}</p>}
        {packet ? <div className="assessment-arrival" key={`${packet.query.location_id}:${packet.query.valid_utc}:${resultSource?.kind}`}><AssessmentFacts packet={packet} sourceKind={resultSource?.kind} onOpenEvidence={showEvidence} /></div> : loading ? <div className="empty-state" role="status"><span className="typing-indicator" aria-hidden="true"><i /><i /><i /></span><p>{querying ? "Checking current station data…" : "Opening assessment…"}</p>{pendingKind && !queryOpen && <button className="cancel-empty" onClick={cancel}>Cancel</button>}</div> : <div className="empty-state"><Icon name="location" size={24} /><h2>Check a location</h2><p>Query current weather or open a saved assessment.</p></div>}
        </>}
      </div>
      <footer className="assessment-controls">
        {queryMounted && <form ref={queryForm} id="location-query" className="query-panel" data-open={queryOpen} inert={!queryOpen ? true : undefined} noValidate onSubmit={submitQuery} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); closeQuery(); } }} aria-label="Query location">
          <div className="query-heading"><strong>Check a location</strong><button type="button" className="icon-button" onClick={closeQuery} aria-label="Close query form"><Icon name="close" /></button></div>
          <label className="query-preset">Location<select value={draft.preset} onChange={choosePreset}><option value="">Custom location</option>{context?.station_presets.map((station) => <option key={station.station_id} value={station.station_id}>{station.name}</option>)}</select></label>
          <div className="coordinate-fields"><label>Latitude<input ref={latitudeInput} type="number" step="any" min="-90" max="90" inputMode="decimal" value={draft.lat} onChange={(event) => editField("lat", event.target.value)} placeholder="38.6000" required /></label><label>Longitude<input type="number" step="any" min="-180" max="180" inputMode="decimal" value={draft.lon} onChange={(event) => editField("lon", event.target.value)} placeholder="−121.8400" required /></label><label>Elevation · ft<input type="number" step="any" inputMode="decimal" value={draft.elevation} onChange={(event) => editField("elevation", event.target.value)} placeholder="70" required /></label></div>
          {queryError && <p className="query-error" role="alert">{queryError}</p>}
          {contextLoading ? <p className="query-connection" role="status">Checking weather connection…</p> : contextError || !context?.configured ? <div className="query-connection"><p>{contextError || "Current weather isn’t connected yet. Saved assessments remain available."}</p><button type="button" onClick={() => setContextRetry((value) => value + 1)}>Retry connection</button></div> : <p className="query-connection">CIMIS hourly readings · checked on demand</p>}
          <div className="query-actions">{farm.available && <button type="button" className="setup-link" onClick={startTaraFromLocation}>Monitor this location</button>}{querying && <button type="button" className="query-cancel" onClick={cancel}>Cancel</button>}<button className="query-submit" type="submit" disabled={querying || contextLoading || !context?.configured}>{querying ? "Checking…" : "Check location"}<Icon name="location" size={14} /></button></div>
        </form>}
        {(controlsMounted || sourceChanging) && <div ref={controlsForm} id="chat-add-menu" className="source-panel" data-open={controlsOpen} inert={!controlsOpen ? true : undefined} aria-label="Assessment sources">
          <div className="query-heading"><strong>Add to chat</strong><button className="icon-button" onClick={() => { setControlsOpen(false); controlsTrigger.current?.focus(); }} aria-label="Close add menu" disabled={sourceChanging}><Icon name="close" size={15} /></button></div>
          <SimulationConnection orchardId={tara.state?.orchard_id} locked={tara.locked} onBusy={setSourceChanging} />
          <details className="source-advanced" inert={sourceChanging ? true : undefined}><summary>Saved assessments and settings</summary>
          {(tara.state?.orchards || farm.orchards).length > 0 && <div className="monitor-source"><label>Locations<select aria-label="Conversation farm" value={tara.state?.orchard_id || ""} disabled={tara.locked} onChange={(event) => { chooseTaraFarm(event.target.value); openMonitor(); }}><option value="" disabled>Farm setup</option>{(tara.state?.orchards || farm.orchards).map((orchard) => <option key={orchard.id} value={orchard.id}>{orchard.name}{orchard.demo_location ? " · demo" : ""}</option>)}</select></label><button type="button" className="open-file" onClick={openMonitor}><Icon name="location" size={16} /><span>Open Terra</span><Icon name="chevron" size={12} /></button>{tara.state?.setup.stage === "complete" && <button type="button" className="open-file" disabled={tara.locked} onClick={async () => { if (await tara.setup("restart")) { openMonitor(); controlsTrigger.current?.focus(); } }}><Icon name="plus" size={16} /><span>Set up another farm</span><Icon name="chevron" size={12} /></button>}</div>}
          <button type="button" className="open-file" onClick={() => { setControlsOpen(false); toggleQuery(); }}><Icon name="location" size={16} /><span>Check a location</span><Icon name="chevron" size={12} /></button>
          <button type="button" className="open-file" onClick={() => { setControlsOpen(false); setQueryOpen(false); setConnectionRequest((value) => value + 1); }}><Icon name="file" size={16} /><span>Message writing</span><Icon name="chevron" size={12} /></button>
          <label htmlFor="assessment-example" className="source-label">Saved assessments</label><select id="assessment-example" value={selectedId} onChange={selectExample} disabled={!entries.length}><option value="" disabled>{resultSource?.kind === "file" ? resultSource.label : "Choose an example"}</option>{entries.map((entry) => <option key={entry.id} value={entry.id}>{entry.title}</option>)}</select><button className="open-file" onClick={() => fileInput.current?.click()}><Icon name="upload" size={16} /><span>Open assessment JSON</span><span className="file-extension">.json</span></button><p className="preview-note">{previewNote}</p></details>
        </div>}
        {view === "monitor" ? <TaraComposer conversation={tara} replyTo={selectedMessage} onClearReply={() => setSelectedMessage(null)} leadingControl={<button ref={controlsTrigger} type="button" className="composer-source icon-button" onClick={() => { setQueryOpen(false); setControlsOpen((value) => !value); }} aria-expanded={controlsOpen} aria-label="Add to chat" aria-controls="chat-add-menu" disabled={sourceChanging}><Icon name="plus" size={19} /></button>} /> : <><div className="composer"><button ref={controlsTrigger} className="composer-source icon-button" onClick={() => { setQueryOpen(false); setControlsOpen((value) => !value); }} aria-expanded={controlsOpen} aria-label="Add to chat" aria-controls="chat-add-menu" disabled={sourceChanging} title="Saved assessments and local JSON"><Icon name="plus" size={19} /></button><button ref={queryTrigger} className="query-toggle" onClick={toggleQuery} aria-expanded={queryOpen} aria-controls="location-query"><span>Check a location</span><span className="composer-send"><Icon name="send" size={15} /></span></button></div><button className="tara-text-button tara-view-return" type="button" onClick={openMonitor}>Back to Terra</button></>}
        <input ref={fileInput} type="file" accept=".json,application/json" onChange={openFile} className="sr-only" tabIndex={-1} aria-label="Open local assessment JSON" />
      </footer>
    </section>
    {panelMounted && panelReady && <EvidencePanel packet={panelPacket} record={panelRecord} contextTime={view === "monitor" ? publicationTime(currentFarmDetails?.message) : undefined} overview={view === "monitor" ? monitorOverview : null} primaryLabel={primaryLabel} sourceKind={view === "monitor" ? "monitor" : resultSource?.kind} finding={view === "monitor" ? null : evidenceFinding} comparison={view === "monitor" ? null : evidenceComparison} tab={panelTab} onTab={setPanelTab} mobile={mobile} onClose={closeJson} closeRef={closeButton} jsonRef={jsonPre} open={showPanel} />}
  </main>;
}
