import React, { useCallback, useEffect, useRef, useState } from "react";

const SCHEMA = "terra-location-assessment-v1";
const ESTIMATE_STATUSES = new Set(["ok", "stale", "insufficient_data"]);
const COMPARISON_STATUSES = new Set(["unassessable", "unavailable", "stale", "assessable", "comparison_only"]);
const DATA_MODES = new Set(["historical_replay", "current", "injected", "synthetic"]);
const STATIONS = { 6: "Davis", 226: "Woodland", 139: "Winters", 212: "Hastings Tract East", 235: "Verona" };
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const finiteOrNull = (value) => value === null || Number.isFinite(value);
const words = (value) => String(value || "Not supplied").replaceAll("_", " ");
const stationName = (id) => STATIONS[id] || `Station ${id}`;
const temperature = (value) => Number.isFinite(value) ? `${value.toFixed(1)}°F` : "Unavailable";
const departure = (value) => Number.isFinite(value) ? `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(1)}°F` : "Unavailable";
const timestamp = (value) => {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not supplied";
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)) + " UTC";
};

function Icon({ name, size = 16 }) {
  const paths = {
    leaf: <><path d="M19 4C11 3 4 6 4 12a6 6 0 0 0 12 0c0-3 1-6 3-8Z" /><path d="m7 17 7-8" /></>,
    file: <><path d="M7 3h7l4 4v14H7Z" /><path d="M14 3v5h4M10 12h5M10 16h5" /></>,
    copy: <><rect x="8" y="8" width="11" height="12" rx="2" /><path d="M15 8V4H4v12h4" /></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" /></>,
    back: <path d="m14 5-7 7 7 7" />,
    chevron: <path d="m8 5 7 7-7 7" />,
    check: <path d="m5 12 4 4L19 6" />,
    upload: <><path d="M12 16V4m-4 4 4-4 4 4M4 17v4h16v-4" /></>,
    refresh: <><path d="M19 8a8 8 0 1 0 1 7M19 3v6h-6" /></>,
    location: <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z" /><circle cx="12" cy="10" r="2" /></>,
    close: <path d="m6 6 12 12M18 6 6 18" />,
  };
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{paths[name] || paths.file}</svg>;
}

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

function Values({ observed, expected, difference, typicalLow, typicalHigh }) {
  const hasRange = [typicalLow, typicalHigh].every(Number.isFinite);
  return <div className="compact-finding"><div className="message-facts"><span><small>Observed</small><strong>{temperature(observed)}</strong></span><span><small>Expected</small><strong>{temperature(expected)}</strong></span></div>{hasRange && <div className="message-range"><span>Typical range</span><strong>{typicalLow.toFixed(1)}–{typicalHigh.toFixed(1)}°F</strong></div>}<div className="message-departure"><span>Departure</span><strong>{departure(difference)}</strong></div></div>;
}

function Disclosure({ title, children }) {
  return <details className="disclosure"><summary><Icon name="file" /><span>{title}</span><Icon name="chevron" size={13} /></summary><div className="disclosure-body">{children}</div></details>;
}

function AssessmentFacts({ packet, onOpenJson }) {
  const { query, location_estimate: estimate, target_comparison: comparison, provenance } = packet;
  const hasTarget = comparison.station_id !== null && comparison.station_id !== undefined;
  const unavailable = estimate.status !== "ok";
  const relevantFindings = hasTarget ? packet.station_findings.filter((finding) => finding.station_id !== comparison.station_id) : packet.station_findings;
  const live = provenance.live_source;
  const sourceWarning = Boolean(live && (live.status === "cached_after_error" || live.warning || live.missing_station_ids?.length || live.failed_station_ids?.length));
  return <>
    <div className="assessment-time"><time dateTime={query.valid_utc}>{timestamp(query.valid_utc)}</time></div>
    <div className="data-mode">{packet.data_mode === "current" && live ? `Current query · ${words(live.status)}` : words(packet.data_mode)}</div>
    <div className="location-context"><span>{query.lat.toFixed(4)}, {query.lon.toFixed(4)}</span><span>{query.elevation_ft.toLocaleString()} ft</span></div>
    {sourceWarning && <div className="availability-status source-warning" role="status"><strong>{live.status === "cached_after_error" ? "Using saved weather after a refresh failure" : "Some station data is unavailable"}</strong><span>{live.status === "cached_after_error" ? "The last successfully retrieved readings are retained. Check their reading time before relying on them." : live.warning || "The assessment uses the source readings that were available."}</span></div>}
    {unavailable && <div className="availability-status" role="status"><strong>{estimate.status === "stale" ? "Estimate is stale" : "Location estimate unavailable"}</strong><span>{words(estimate.status_reason)}</span>{estimate.status === "stale" && <small>The retained value describes the reading time shown above.</small>}</div>}
    <div className="messages">
      {hasTarget && <section className="message-group" aria-label="Measured station comparison">
        <div className="message-meta"><span>{stationName(comparison.station_id)}</span><span>Measured comparison</span></div>
        {comparison.status !== "assessable" && <p className="comparison-notice">{comparison.status === "comparison_only" ? "Comparison only · no calibrated alert policy" : `${words(comparison.status)} · ${words(comparison.reason)}`}</p>}
        <div className="incoming-bubble"><Values observed={comparison.observed_f} expected={comparison.expected_f} difference={comparison.departure_f} typicalLow={comparison.typical_low_f} typicalHigh={comparison.typical_high_f} /></div>
        <p className="message-caption">{comparison.status !== "assessable" ? "No current sensor-fault conclusion is available." : comparison.classification === "suspected_anomaly" ? "Suspected anomaly · check an independent reading." : comparison.classification === "no_alert" ? "No alert triggered by the frozen station policy." : "No sensor-fault conclusion is available."}</p>
      </section>}
      <section className={`message-group${hasTarget ? " secondary-estimate" : ""}`} aria-label="Location temperature estimate">
        <div className="message-meta"><span>{hasTarget ? "Location interpolation" : "At this location"}</span>{!hasTarget && <span>Estimate</span>}</div>
        <div className="incoming-bubble estimate-bubble">{!hasTarget && <small>Estimated temperature</small>}<strong className="estimate-value">{temperature(estimate.temperature)}</strong><span className="estimate-support">{estimate.n_used ?? estimate.used_station_ids.length} usable stations · {estimate.method === "idw_lapse" ? "IDW + lapse" : words(estimate.method)}</span></div>
        {!hasTarget && <p className="message-caption">No local sensor reading supplied.</p>}
      </section>
      <section className="message-group station-findings" aria-label={hasTarget ? "Other relevant station flags" : "Relevant station flags"}>
        <div className="message-meta"><span>{hasTarget ? "Other relevant station flags" : "Relevant station flags"}</span><span>{relevantFindings.length}</span></div>
        {relevantFindings.length ? relevantFindings.map((finding, index) => {
          const summary = finding.summary || {};
          const peak = finding.evidence_peak_utc || summary.peak_timestamp || summary.peak_utc;
          const hasValues = [summary.observed_f, summary.expected_f, summary.departure_f].every(Number.isFinite);
          return <article className="station-note" key={`${finding.station_id}:${finding.start_utc || index}`}>
            <div className="station-note-heading"><strong>{stationName(finding.station_id)}</strong><span>{words(finding.reason)}</span></div>
            <div className="incoming-bubble">{hasValues ? <Values observed={summary.observed_f} expected={summary.expected_f} difference={summary.departure_f} typicalLow={summary.typical_low_f} typicalHigh={summary.typical_high_f} /> : <p className="note-text">{finding.message || "A suspected temperature departure is recorded."}</p>}</div>
            <p className="message-caption">{peak ? `Evidence peak · ${timestamp(peak)}` : `Detected · ${timestamp(finding.detected_at_utc)}`}{finding.freshness === "stale" ? " · stale" : ""}</p>
          </article>;
        }) : <p className="no-findings">{hasTarget ? "No other station flags in this packet." : "No station flags in this packet."}</p>}
      </section>
      <button className="attachment" onClick={onOpenJson}><Icon name="file" size={22} /><span><strong>Assessment JSON</strong><small>Exact values and evidence</small></span><Icon name="chevron" size={14} /></button>
    </div>
    <div className="details-list">
      <Disclosure title="Details"><FactRows values={[["Reading time", timestamp(query.valid_utc)], ["As of", timestamp(query.as_of_utc)], ["Location estimate", words(estimate.status)], ["Local comparison", words(comparison.status)], ["Peer count", estimate.n_used], ["Geometry", estimate.geometry_source], ["Maximum age", Number.isFinite(query.max_age_hours) ? `${query.max_age_hours} h` : "Not set"]]} /><p className="microcopy">Location interpolation and the frozen station comparison are separate calculations. A typical range describes the station's calibrated historical residual band; it is not a forecast or a guaranteed confidence interval.</p></Disclosure>
      <Disclosure title="Sources and exclusions"><FactRows values={[["Used stations", estimate.used_station_ids.map(stationName).join(", ") || "None"], ["Interpolation", estimate.method_version], ["QC reference", provenance.qc_reference_id], ["Data snapshot", provenance.data_snapshot_id], ["Publication visibility", words(provenance.publication_visibility)]]} />{live && <><p className="detail-label">Current source check</p><FactRows values={[["Provider", live.provider], ["Checked", timestamp(live.checked_at_utc)], ["Retrieved", timestamp(live.retrieved_at_utc)], ["Data through", timestamp(live.data_through_utc)], ["Source state", words(live.status)], ["Cache age", Number.isFinite(live.cache_age_seconds) ? `${Math.round(live.cache_age_seconds)} seconds` : null], ["Missing stations", live.missing_station_ids?.length ? live.missing_station_ids.map(stationName).join(", ") : null], ["Failed stations", live.failed_station_ids?.length ? live.failed_station_ids.map(stationName).join(", ") : null]]} /></>}{estimate.excluded.length > 0 && <><p className="detail-label">Excluded from the estimate</p><ul className="exclusion-list">{estimate.excluded.map((item, index) => <li key={`${item.station_id}:${index}`}><span>{stationName(item.station_id)}</span><span>{words(item.reason)}</span></li>)}</ul></>}{packet.data_health?.length > 0 && <><p className="detail-label">Source availability</p><pre className="detail-json">{JSON.stringify(packet.data_health, null, 2)}</pre></>}{packet.estimator_qc_exclusions?.length > 0 && <><p className="detail-label">Target-held-out peer exclusions</p><pre className="detail-json">{JSON.stringify(packet.estimator_qc_exclusions, null, 2)}</pre></>}</Disclosure>
    </div>
  </>;
}

function JsonPanel({ packet, mobile, onClose, closeRef, jsonRef }) {
  const [copyResult, setCopyResult] = useState(null);
  const json = JSON.stringify(packet, null, 2);
  const copyStatus = copyResult?.json === json ? copyResult.message : "";
  async function copy() {
    try { await navigator.clipboard.writeText(json); setCopyResult({json, message: "Copied"}); }
    catch { setCopyResult({json, message: "Copy unavailable. Use Download."}); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([json + "\n"], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `terra-${String(packet.query.location_id || "assessment").replace(/[^a-zA-Z0-9_-]/g, "-")}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function onKeyDown(event) {
    if (!mobile) return;
    if (event.key === "Escape") { event.preventDefault(); onClose(); }
    if (event.key !== "Tab") return;
    const elements = [...event.currentTarget.querySelectorAll("button, [tabindex='0']")];
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  return <aside className="artifact-panel" role={mobile ? "dialog" : undefined} aria-modal={mobile ? true : undefined} aria-label="Assessment JSON" onKeyDown={onKeyDown}>
    <header className="artifact-header">{mobile && <button ref={closeRef} className="icon-button back-button" onClick={onClose} aria-label="Back to assessment"><Icon name="back" /></button>}<span><Icon name="file" />JSON</span><div className="artifact-actions"><button className="icon-button" onClick={copy} disabled={!packet} title="Copy JSON" aria-label="Copy assessment JSON"><Icon name={copyStatus === "Copied" ? "check" : "copy"} /></button><button className="icon-button" onClick={download} title="Download JSON" aria-label="Download assessment JSON"><Icon name="download" /></button></div></header>
    <div className="artifact-content"><div className="json-caption"><span>{SCHEMA}</span><span role="status">{copyStatus}</span></div><pre ref={jsonRef} className="evidence-json" tabIndex={0} aria-label="Exact assessment packet">{json}</pre></div>
  </aside>;
}

export function App() {
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
  const [draft, setDraft] = useState({ lat: "", lon: "", elevation: "", preset: "" });
  const [queryError, setQueryError] = useState("");
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 700px)").matches);
  const [mobileJson, setMobileJson] = useState(false);
  const fileInput = useRef(null), closeButton = useRef(null), jsonPre = useRef(null), opener = useRef(null);
  const queryTrigger = useRef(null), queryForm = useRef(null), latitudeInput = useRef(null);
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
        const returnFocus = queryForm.current?.contains(document.activeElement);
        setPacket(value); setResultSource({ kind: "query", label: "Current weather query" }); setQueryOpen(false);
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
      if (active(task)) { setSelectedId(""); setPacket(value); setResultSource({ kind: "file", label: file.name }); setMobileJson(false); }
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
  useEffect(() => { if (queryOpen) latitudeInput.current?.focus(); }, [queryOpen]);

  function showJson(event) {
    opener.current = event.currentTarget;
    if (mobile) setMobileJson(true);
    else jsonPre.current?.focus();
  }
  function closeJson() { setMobileJson(false); requestAnimationFrame(() => opener.current?.focus()); }
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
    userSelection.current = true; setMobileJson(false); setQueryOpen(false); loadExample(entry);
  }
  function openFile(event) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    userSelection.current = true; setQueryOpen(false); loadFile(file);
  }
  function toggleQuery() {
    if (!queryOpen && !draftEdited.current && packet) {
      const preset = context?.station_presets.find((station) => station.station_id === packet.query.target_station_id);
      setDraft({ lat: String(packet.query.lat), lon: String(packet.query.lon), elevation: String(packet.query.elevation_ft), preset: preset ? String(preset.station_id) : "" });
    }
    setQueryError(""); setQueryOpen((value) => !value);
  }
  function closeQuery() { setQueryOpen(false); requestAnimationFrame(() => queryTrigger.current?.focus()); }
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

  const showPanel = Boolean(packet) && (!mobile || mobileJson);
  const querying = loading && pendingKind === "query";
  const previewNote = resultSource?.kind === "query" ? "Provider query · checked on demand" : resultSource?.kind === "file" ? `Local file · ${resultSource.label}` : resultSource?.description || "Saved assessment · no message delivery";
  return <main className={`app-window${showPanel ? " with-artifacts" : ""}`}>
    <section className="conversation" inert={mobile && mobileJson ? true : undefined} aria-hidden={mobile && mobileJson ? true : undefined}>
      <header className="thread-header"><div className="identity"><span className="terra-icon"><Icon name="leaf" size={18} /></span><div><h1>Terra</h1><span>Location assessment</span></div></div><span className="preview-badge">Local queries</span></header>
      <div className="message-scroll" aria-busy={loading}>
        {error && <div className="load-error" role="alert"><p>{error}</p>{retryable && <button onClick={retry}><Icon name="refresh" size={14} />{lastAttempt.current?.kind === "query" ? "Retry query" : "Retry"}</button>}</div>}
        {loading && packet && <div className="request-status" role="status"><span>{querying ? "Checking location…" : "Opening assessment…"}<small>Previous result shown below.</small></span>{!queryOpen && <button onClick={cancel}>Cancel</button>}</div>}
        {notice && <p className="request-notice" role="status">{notice}</p>}
        {packet ? <AssessmentFacts packet={packet} onOpenJson={showJson} /> : loading ? <div className="empty-state" role="status"><Icon name="leaf" size={24} /><p>{querying ? "Checking current station data…" : "Opening assessment…"}</p>{pendingKind && !queryOpen && <button className="cancel-empty" onClick={cancel}>Cancel</button>}</div> : <div className="empty-state"><Icon name="location" size={24} /><h2>Check a location</h2><p>Query current weather or open a saved assessment.</p></div>}
      </div>
      <footer className="assessment-controls">
        {queryOpen && <form ref={queryForm} id="location-query" className="query-panel" noValidate onSubmit={submitQuery} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); closeQuery(); } }} aria-label="Query location">
          <div className="query-heading"><strong>Query location</strong><button type="button" className="icon-button" onClick={closeQuery} aria-label="Close query form"><Icon name="close" /></button></div>
          <label className="query-preset">Location<select value={draft.preset} onChange={choosePreset}><option value="">Custom coordinate · no local sensor</option>{context?.station_presets.map((station) => <option key={station.station_id} value={station.station_id}>{station.name} · measured station</option>)}</select></label>
          <div className="coordinate-fields"><label>Latitude<input ref={latitudeInput} type="number" step="any" min="-90" max="90" inputMode="decimal" value={draft.lat} onChange={(event) => editField("lat", event.target.value)} placeholder="38.6000" required /></label><label>Longitude<input type="number" step="any" min="-180" max="180" inputMode="decimal" value={draft.lon} onChange={(event) => editField("lon", event.target.value)} placeholder="−121.8400" required /></label><label>Elevation · ft<input type="number" step="any" inputMode="decimal" value={draft.elevation} onChange={(event) => editField("elevation", event.target.value)} placeholder="70" required /></label></div>
          {queryError && <p className="query-error" role="alert">{queryError}</p>}
          {contextLoading ? <p className="query-connection" role="status">Checking weather connection…</p> : contextError || !context?.configured ? <div className="query-connection"><p>{contextError || "Current weather isn’t connected yet. Saved assessments remain available."}</p><button type="button" onClick={() => setContextRetry((value) => value + 1)}>Retry connection</button></div> : <p className="query-connection">Checks CIMIS readings · estimates may be unavailable or stale.</p>}
          <div className="query-actions">{querying && <button type="button" className="query-cancel" onClick={cancel}>Cancel</button>}<button className="query-submit" type="submit" disabled={querying || contextLoading || !context?.configured}>{querying ? "Checking…" : "Check location"}<Icon name="location" size={14} /></button></div>
        </form>}
        <button ref={queryTrigger} className="query-toggle" onClick={toggleQuery} aria-expanded={queryOpen} aria-controls="location-query"><Icon name="location" size={15} /><span>Query location</span><Icon name="chevron" size={12} /></button>
        <label htmlFor="assessment-example">Saved assessments</label><div className="controls-row"><select id="assessment-example" value={selectedId} onChange={selectExample} disabled={!entries.length}><option value="" disabled>{resultSource?.kind === "file" ? resultSource.label : "Choose an example"}</option>{entries.map((entry) => <option key={entry.id} value={entry.id}>{entry.title}</option>)}</select><button className="open-file" onClick={() => fileInput.current?.click()} title="Open a local assessment JSON"><Icon name="upload" size={15} /><span>Open JSON</span></button><input ref={fileInput} type="file" accept=".json,application/json" onChange={openFile} className="sr-only" tabIndex={-1} aria-label="Open local assessment JSON" /></div><p className="preview-note">{previewNote}</p>
      </footer>
    </section>
    {showPanel && <JsonPanel packet={packet} mobile={mobile} onClose={closeJson} closeRef={closeButton} jsonRef={jsonPre} />}
  </main>;
}
