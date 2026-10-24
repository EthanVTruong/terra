import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { EvidencePanel } from "./EvidencePanel";
import { dataModeLabel, departure, stationName, temperature, timestamp } from "./presentation";
import { FarmConversation, FarmOverview, useFarmFeed } from "./FarmFeed";
import SmsControls from "./SmsControls";
import { usePresence } from "./usePresence";

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

function FactRows({ values }) {
  return <dl className="facts">{values.filter(([, value]) => value !== undefined && value !== null).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>;
}

function Values({ observed, expected, difference }) {
  return <div className="compact-finding"><div className="message-facts"><span><small>Observed</small><strong>{temperature(observed)}</strong></span><span><small>Expected</small><strong>{temperature(expected)}</strong></span></div><div className="message-departure"><span>Departure</span><strong>{departure(difference)}</strong></div></div>;
}

function Disclosure({ title, children }) {
  return <details className="disclosure"><summary><Icon name="file" /><span>{title}</span><Icon name="chevron" size={13} /></summary><div className="disclosure-body">{children}</div></details>;
}

function AssessmentFacts({ packet, sourceKind, onOpenJson, onOpenEvidence }) {
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
      <button className="attachment" onClick={onOpenJson}><Icon name="file" size={22} /><span><strong>Assessment JSON</strong><small>Exact values · source evidence</small></span><Icon name="chevron" size={14} /></button>
    </div>
    <div className="details-list">
      <Disclosure title="Details"><FactRows values={[["Reading time", timestamp(query.valid_utc)], ["As of", timestamp(query.as_of_utc)], ["Location estimate", words(estimate.status)], ["Local comparison", words(comparison.status)], ["Peer count", estimate.n_used], ["Geometry", estimate.geometry_source], ["Maximum age", Number.isFinite(query.max_age_hours) ? `${query.max_age_hours} h` : "Not set"]]} /><p className="microcopy">Location interpolation and the frozen station comparison are separate calculations. A typical range describes the station's calibrated historical residual band; it is not a forecast or a guaranteed confidence interval.</p></Disclosure>
      <Disclosure title="Sources and exclusions"><FactRows values={[["Used stations", estimate.used_station_ids.map(stationName).join(", ") || "None"], ["Interpolation", estimate.method_version], ["QC reference", provenance.qc_reference_id], ["Data snapshot", provenance.data_snapshot_id], ["Publication visibility", words(provenance.publication_visibility)]]} />{live && <><p className="detail-label">Current source check</p><FactRows values={[["Provider", live.provider], ["Checked", timestamp(live.checked_at_utc)], ["Retrieved", timestamp(live.retrieved_at_utc)], ["Data through", timestamp(live.data_through_utc)], ["Source state", words(live.status)], ["Cache age", Number.isFinite(live.cache_age_seconds) ? `${Math.round(live.cache_age_seconds)} seconds` : null], ["Missing stations", live.missing_station_ids?.length ? live.missing_station_ids.map(stationName).join(", ") : null], ["Failed stations", live.failed_station_ids?.length ? live.failed_station_ids.map(stationName).join(", ") : null]]} /></>}{estimate.excluded.length > 0 && <><p className="detail-label">Excluded from the estimate</p><ul className="exclusion-list">{estimate.excluded.map((item, index) => <li key={`${item.station_id}:${index}`}><span>{stationName(item.station_id)}</span><span>{words(item.reason)}</span></li>)}</ul></>}{packet.data_health?.length > 0 && <><p className="detail-label">Source availability</p><pre className="detail-json">{JSON.stringify(packet.data_health, null, 2)}</pre></>}{packet.estimator_qc_exclusions?.length > 0 && <><p className="detail-label">Target-held-out peer exclusions</p><pre className="detail-json">{JSON.stringify(packet.estimator_qc_exclusions, null, 2)}</pre></>}</Disclosure>
    </div>
  </>;
}

export function App() {
  const [view, setView] = useState("assessment");
  const [smsStatus, setSmsStatus] = useState(null);
  const [monitorSetup, setMonitorSetup] = useState(false);
  const [orchardName, setOrchardName] = useState("Demo orchard");
  const [monitoringBusy, setMonitoringBusy] = useState(false);
  const [farmDetails, setFarmDetails] = useState(null);
  const [selectedMessage, setSelectedMessage] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false), [detailsError, setDetailsError] = useState("");
  const farm = useFarmFeed(view === "monitor");
  const detailRequest = useRef({ version: 0, controller: null }), setupIdentity = useRef(null), nearBottom = useRef(true);
  const orchardNameInput = useRef(null);
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
  const [context, setContext] = useState(null);
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
  const userSelection = useRef(false), draftEdited = useRef(false), lastAttempt = useRef(null);
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
  useEffect(() => { if (monitorSetup) orchardNameInput.current?.focus(); }, [monitorSetup]);
  useEffect(() => {
    if (farm.orchards.length && !userSelection.current) setView("monitor");
  }, [farm.orchards]);
  useEffect(() => {
    detailRequest.current.controller?.abort(); detailRequest.current.version += 1;
    setFarmDetails(null); setSelectedMessage(null); setDetailsLoading(false); setDetailsError("");
  }, [farm.orchardId]);
  useEffect(() => () => detailRequest.current.controller?.abort(), []);
  useEffect(() => {
    if (view === "monitor" && nearBottom.current) requestAnimationFrame(() => {
      const container = messageScroll.current;
      container?.scrollTo({ top: container.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  }, [farm.feed?.messages, view]);
  useEffect(() => { if (queryOpen) latitudeInput.current?.focus(); }, [queryOpen]);
  useEffect(() => { if (controlsOpen) controlsForm.current?.querySelector("select")?.focus(); }, [controlsOpen]);
  useEffect(() => { setSelectedEvidence(null); messageScroll.current?.scrollTo(0, 0); }, [packet]);
  useEffect(() => {
    if (!queryOpen && !controlsOpen) return;
    function dismiss(event) {
      if (queryForm.current?.contains(event.target) || queryTrigger.current?.contains(event.target) || controlsForm.current?.contains(event.target) || controlsTrigger.current?.contains(event.target)) return;
      setQueryOpen(false); setControlsOpen(false);
    }
    function escape(event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (queryOpen) { setQueryOpen(false); queryTrigger.current?.focus(); }
      else { setControlsOpen(false); controlsTrigger.current?.focus(); }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [queryOpen, controlsOpen]);

  function showJson(event) {
    opener.current = event.currentTarget; setPanelTab("json"); setPanelOpen(true);
    if (mobile) setMobileJson(true);
    else requestAnimationFrame(() => jsonPre.current?.focus());
  }
  function showEvidence(event, finding = null, comparison = null) {
    const matching = comparison && packet.station_findings.find((item) => item.station_id === comparison.station_id && Array.isArray(item.evidence) && item.evidence.some((row) => Date.parse(row?.timestamp) === Date.parse(packet.query.valid_utc)));
    opener.current = event.currentTarget; setPanelTab("temperature"); setSelectedEvidence({ finding: finding || matching || null, comparison }); setPanelOpen(true);
    if (mobile) setMobileJson(true);
  }
  function closeJson() { setMobileJson(false); setPanelOpen(false); requestAnimationFrame(() => (opener.current?.isConnected ? opener.current : panelTrigger.current)?.focus()); }
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
    setMonitorSetup(false); setControlsOpen(false);
    if (!queryOpen && !draftEdited.current && packet) {
      const preset = context?.station_presets.find((station) => station.station_id === packet.query.target_station_id);
      setDraft({ lat: String(packet.query.lat), lon: String(packet.query.lon), elevation: String(packet.query.elevation_ft), preset: preset ? String(preset.station_id) : "" });
    }
    setQueryError(""); setQueryOpen((value) => !value);
  }
  function closeQuery() { setQueryOpen(false); requestAnimationFrame(() => queryTrigger.current?.focus()); }
  function editField(field, value) {
    setupIdentity.current = null;
    draftEdited.current = true; setQueryError(""); setDraft((previous) => ({ ...previous, [field]: value, preset: "" }));
  }
  function choosePreset(event) {
    setupIdentity.current = null;
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
    setPanelTab("temperature"); setPanelOpen(true); setMobileJson(false); nearBottom.current = true;
  }

  async function createMonitoring(event) {
    event.preventDefault();
    if (monitoringBusy) return;
    try {
      const values = queryValues(draft), name = orchardName.trim();
      if (!name || name.length > 80) throw new Error("Enter an orchard name of 1–80 characters.");
      setMonitoringBusy(true); setQueryError("");
      const sessionResponse = await fetch("/api/sms/status");
      if (!sessionResponse.ok) throw new Error("Reconnect to the local Terra service to set up monitoring.");
      const session = await sessionResponse.json();
      if (typeof session.control_token !== "string" || !session.control_token) throw new Error("Reload the local page to authorize monitoring setup.");
      setSmsStatus(session);
      setupIdentity.current ||= "orchard-" + crypto.randomUUID();
      const response = await fetch("/api/orchards", { method: "POST", headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": session.control_token }, body: JSON.stringify({ name, orchard_id: setupIdentity.current, lat: values.lat, lon: values.lon, elevation_ft: values.elevation_ft }) });
      if (!response.ok) throw new Error(response.status === 403 ? "Reload the local page, then retry monitoring setup." : "Monitoring could not be set up. Check the location and retry.");
      const profile = await response.json();
      if (typeof profile.id !== "string" || typeof profile.name !== "string") throw new Error("The saved location could not be read. Reload monitoring before retrying.");
      const returnFocus = queryForm.current?.dataset.open === "true" && (document.activeElement === document.body || queryForm.current.contains(document.activeElement));
      farm.addOrchard(profile); setFarmDetails(null); setSelectedMessage(null); openMonitor();
      if (returnFocus) requestAnimationFrame(() => queryTrigger.current?.focus());
    } catch (reason) { setQueryError(reason instanceof TypeError ? "The local monitoring service could not be reached. Retry setup." : reason.message); }
    finally { setMonitoringBusy(false); }
  }

  async function openFarmDetails(event, message) {
    if (detailsLoading && selectedMessage === message.id) return;
    opener.current = event.currentTarget; setPanelTab("temperature"); setPanelOpen(true);
    if (mobile) setMobileJson(true);
    detailRequest.current.controller?.abort();
    const task = { version: detailRequest.current.version + 1, controller: new AbortController() };
    detailRequest.current = task;
    setSelectedMessage(message.id); setFarmDetails(null); setDetailsLoading(true); setDetailsError("");
    try {
      const response = await fetch("/api/orchards/" + encodeURIComponent(farm.orchardId) + "/messages/" + encodeURIComponent(message.id) + "/details", { signal: task.controller.signal });
      if (!response.ok) throw new Error("The note’s evidence is unavailable. Open Details to retry.");
      const value = await response.json();
      if (value.message?.id !== message.id || value.message?.orchard_id !== farm.orchardId || !Array.isArray(value.assessments) || !Array.isArray(value.events)) throw new Error("The note’s evidence could not be read.");
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

  const farmPackets = (farmDetails?.assessments || []).flatMap((item) => {
    try { return [validateAssessment(item)]; } catch { return []; }
  }).sort((a, b) => Date.parse(a.query.valid_utc) - Date.parse(b.query.valid_utc));
  const panelPacket = view === "monitor" ? farmPackets.at(-1) || null : packet;
  const panelRecord = view === "monitor" ? farmDetails || farm.feed : null;
  const panelReady = view === "monitor" ? Boolean(farm.orchardId) : Boolean(packet);
  const primaryLabel = view === "monitor" && !panelPacket ? "Chill" : "Temperature";
  const monitorOverview = detailsLoading ? <div className="empty-state" role="status"><span className="typing-indicator" aria-hidden="true"><i /><i /><i /></span><p>Opening evidence…</p></div> : panelPacket ? <details className="disclosure farm-extra"><summary><Icon name="file" /><span>Chill and monitoring</span><Icon name="chevron" size={12} /></summary><div className="disclosure-body"><FarmOverview farm={farm} details={farmDetails} /></div></details> : <FarmOverview farm={farm} details={farmDetails} />;
  const showPanel = panelReady && (mobile ? mobileJson : panelOpen);
  const panelMounted = usePresence(showPanel, 220);
  const queryMounted = usePresence(queryOpen, 180);
  const controlsMounted = usePresence(controlsOpen, 180);
  const evidenceFinding = selectedEvidence?.finding || (!selectedEvidence?.comparison ? packet?.station_findings[0] : null);
  const evidenceComparison = selectedEvidence?.comparison || (!evidenceFinding && packet?.target_comparison.station_id !== null ? packet?.target_comparison : null);
  const querying = loading && pendingKind === "query";
  const previewNote = resultSource?.kind === "query" ? "Provider query · checked on demand" : resultSource?.kind === "file" ? `Local file · ${resultSource.label}` : resultSource?.description || "Saved assessment · no message delivery";
  return <main className={`app-window${showPanel ? " with-artifacts" : ""}${panelMounted ? " panel-mounted" : ""}`}>
    <section className="conversation" inert={mobile && mobileJson ? true : undefined} aria-hidden={mobile && mobileJson ? true : undefined}>
      <header className="thread-header"><h1 className="brand-wordmark"><img src={`${import.meta.env.BASE_URL}brand/terra-wordmark-1080.png`} alt="terra" /></h1><div className="thread-actions"><span className={`presence${querying ? " is-working" : ""}`} title={querying ? "Checking current station data" : "On-demand location checks"}>{querying ? "Checking…" : view === "monitor" ? farm.feed?.monitor.worker?.heartbeat_status === "fresh" ? "Monitoring" : farm.feed?.monitor.worker?.heartbeat_status === "stale_or_stopped" ? "Updates unavailable" : "On demand" : "Field notes"}</span><SmsControls onAssessment={showSmsAssessment} onStatus={setSmsStatus} orchard={farm.orchard} /><button ref={panelTrigger} className={`icon-button${showPanel ? " selected" : ""}`} onClick={(event) => { opener.current = event.currentTarget; if (showPanel) closeJson(); else { setPanelOpen(true); if (mobile) setMobileJson(true); } }} aria-label={showPanel ? "Hide evidence panel" : "Show evidence panel"} aria-expanded={showPanel}><Icon name="panel" size={17} /></button></div></header>
      <div ref={messageScroll} className="message-scroll" aria-busy={view === "monitor" ? farm.loading : loading} onScroll={(event) => { const node = event.currentTarget; nearBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48; }}>
        {view === "monitor" ? <>{error && userSelection.current && <div className="load-error" role="alert"><p>{error}</p>{retryable && <button onClick={retry}>Retry {lastAttempt.current?.kind === "query" ? "query" : "request"}</button>}</div>}{loading && pendingKind === "query" && <div className="request-status" role="status"><span>Checking location…</span><button onClick={cancel}>Cancel</button></div>}{notice && <p className="request-notice" role="status">{notice}</p>}<FarmConversation farm={farm} onDetails={openFarmDetails} selectedMessage={selectedMessage} detailsLoading={detailsLoading} detailsError={detailsError} /></> : <>
        {error && <div className="load-error" role="alert"><p>{error}</p>{retryable && <button onClick={retry}><Icon name="refresh" size={14} />{lastAttempt.current?.kind === "query" ? "Retry query" : "Retry"}</button>}</div>}
        {loading && packet && <div className="request-status" role="status"><span>{querying ? "Checking location…" : "Opening assessment…"}<small>Previous result shown below.</small></span>{!queryOpen && <button onClick={cancel}>Cancel</button>}</div>}
        {notice && <p className="request-notice" role="status">{notice}</p>}
        {packet ? <div className="assessment-arrival" key={`${packet.query.location_id}:${packet.query.valid_utc}:${resultSource?.kind}`}><AssessmentFacts packet={packet} sourceKind={resultSource?.kind} onOpenJson={showJson} onOpenEvidence={showEvidence} /></div> : loading ? <div className="empty-state" role="status"><span className="typing-indicator" aria-hidden="true"><i /><i /><i /></span><p>{querying ? "Checking current station data…" : "Opening assessment…"}</p>{pendingKind && !queryOpen && <button className="cancel-empty" onClick={cancel}>Cancel</button>}</div> : <div className="empty-state"><Icon name="location" size={24} /><h2>Check a location</h2><p>Query current weather or open a saved assessment.</p></div>}
        </>}
      </div>
      <footer className="assessment-controls">
        {queryMounted && <form ref={queryForm} id="location-query" className="query-panel" data-open={queryOpen} inert={!queryOpen ? true : undefined} noValidate onSubmit={monitorSetup ? createMonitoring : submitQuery} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); closeQuery(); } }} aria-label="Query location">
          <div className="query-heading"><strong>{monitorSetup ? "Monitor this location" : "Check a location"}</strong><button type="button" className="icon-button" onClick={closeQuery} aria-label="Close query form"><Icon name="close" /></button></div>
          <label className="query-preset">Location<select value={draft.preset} onChange={choosePreset}><option value="">Custom location</option>{context?.station_presets.map((station) => <option key={station.station_id} value={station.station_id}>{station.name}</option>)}</select></label>
          <div className="coordinate-fields"><label>Latitude<input ref={latitudeInput} type="number" step="any" min="-90" max="90" inputMode="decimal" value={draft.lat} onChange={(event) => editField("lat", event.target.value)} placeholder="38.6000" required /></label><label>Longitude<input type="number" step="any" min="-180" max="180" inputMode="decimal" value={draft.lon} onChange={(event) => editField("lon", event.target.value)} placeholder="−121.8400" required /></label><label>Elevation · ft<input type="number" step="any" inputMode="decimal" value={draft.elevation} onChange={(event) => editField("elevation", event.target.value)} placeholder="70" required /></label></div>
          {monitorSetup && <label className="orchard-name">Orchard name<input ref={orchardNameInput} value={orchardName} maxLength={80} onChange={(event) => { setOrchardName(event.target.value); setupIdentity.current = null; }} /><small>Phone alerts start off.</small></label>}
          {queryError && <p className="query-error" role="alert">{queryError}</p>}
          {contextLoading ? <p className="query-connection" role="status">Checking weather connection…</p> : contextError || !context?.configured ? <div className="query-connection"><p>{contextError || "Current weather isn’t connected yet. Saved assessments remain available."}</p><button type="button" onClick={() => setContextRetry((value) => value + 1)}>Retry connection</button></div> : <p className="query-connection">CIMIS hourly readings · checked on demand</p>}
          <div className="query-actions">{farm.available && !monitorSetup && <button type="button" className="setup-link" onClick={() => { setMonitorSetup(true); setQueryError(""); }}>Monitor this location</button>}{querying && <button type="button" className="query-cancel" onClick={cancel}>Cancel</button>}<button className="query-submit" type="submit" disabled={monitorSetup ? monitoringBusy : querying || contextLoading || !context?.configured}>{monitorSetup ? monitoringBusy ? "Setting up…" : "Start monitoring" : querying ? "Checking…" : "Check location"}<Icon name="location" size={14} /></button></div>
        </form>}
        {controlsMounted && <div ref={controlsForm} className="source-panel" data-open={controlsOpen} inert={!controlsOpen ? true : undefined} aria-label="Assessment sources"><div className="query-heading"><strong>Sources</strong><button className="icon-button" onClick={() => { setControlsOpen(false); controlsTrigger.current?.focus(); }} aria-label="Close saved assessments"><Icon name="close" size={15} /></button></div>{farm.available && <div className="monitor-source"><label>Monitoring<select value={farm.orchardId} onChange={(event) => { farm.selectOrchard(event.target.value); openMonitor(); }}><option value="" disabled>No locations yet</option>{farm.orchards.map((orchard) => <option key={orchard.id} value={orchard.id}>{orchard.name}{orchard.demo_location ? " · demo" : ""}</option>)}</select></label>{farm.orchardId && <button className="open-file" onClick={openMonitor}><Icon name="location" size={16} /><span>Open field notes</span><Icon name="chevron" size={12} /></button>}</div>}<label htmlFor="assessment-example" className="source-label">Saved assessments</label><select id="assessment-example" value={selectedId} onChange={selectExample} disabled={!entries.length}><option value="" disabled>{resultSource?.kind === "file" ? resultSource.label : "Choose an example"}</option>{entries.map((entry) => <option key={entry.id} value={entry.id}>{entry.title}</option>)}</select><button className="open-file" onClick={() => fileInput.current?.click()}><Icon name="upload" size={16} /><span>Open assessment JSON</span><span className="file-extension">.json</span></button><p className="preview-note">{previewNote}</p></div>}
        <div className="composer"><button ref={controlsTrigger} className="composer-source icon-button" onClick={() => { setQueryOpen(false); setControlsOpen((value) => !value); }} aria-expanded={controlsOpen} aria-label="Choose assessment source" title="Saved assessments and local JSON"><Icon name="plus" size={19} /></button><button ref={queryTrigger} className="query-toggle" onClick={toggleQuery} aria-expanded={queryOpen} aria-controls="location-query"><span>Check a location</span><span className="composer-send"><Icon name="send" size={15} /></span></button></div>
        <input ref={fileInput} type="file" accept=".json,application/json" onChange={openFile} className="sr-only" tabIndex={-1} aria-label="Open local assessment JSON" />
      </footer>
    </section>
    {panelMounted && panelReady && <EvidencePanel packet={panelPacket} record={panelRecord} overview={view === "monitor" ? monitorOverview : null} primaryLabel={primaryLabel} sourceKind={view === "monitor" ? "monitor" : resultSource?.kind} finding={view === "monitor" ? null : evidenceFinding} comparison={view === "monitor" ? null : evidenceComparison} tab={panelTab} onTab={setPanelTab} mobile={mobile} onClose={closeJson} closeRef={closeButton} jsonRef={jsonPre} open={showPanel} />}
  </main>;
}
