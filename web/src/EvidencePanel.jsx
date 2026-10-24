import React, { lazy, Suspense, useEffect, useState } from "react";
import { Icon } from "./Icon";
import { dataModeLabel, stationName as nameOf, temperature as temp, timestamp } from "./presentation";

const EvidenceChart = lazy(() => import("./EvidenceChart"));

function TemperatureView({ packet, sourceKind, finding, comparison }) {
  const summary = comparison || finding?.summary;
  const evidence = Array.isArray(finding?.evidence) ? finding.evidence.filter((row) => row && Number.isFinite(Date.parse(row.timestamp))).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)) : [];
  const peak = comparison ? packet.query.valid_utc : finding?.evidence_peak_utc || summary?.peak_timestamp || summary?.peak_utc;
  const row = evidence.find((row) => Date.parse(row.timestamp) === Date.parse(peak)) || evidence.at(-1);
  const peers = Array.isArray(row?.peer_evidence) ? row.peer_evidence.filter((peer) => peer && Number.isInteger(peer.station_id)) : [];
  const hasComparison = Boolean(comparison || finding);
  const estimate = packet.location_estimate;
  return <div className="temperature-view">
    <div className="evidence-title"><h2>{comparison ? nameOf(comparison.station_id) : finding ? nameOf(finding.station_id) : "At this location"}</h2><span>{hasComparison ? "Temperature comparison" : "Temperature estimate"}</span></div>
    <p className="evidence-context">{dataModeLabel(packet, sourceKind)}<br />{timestamp(comparison ? packet.query.valid_utc : peak || packet.query.valid_utc)}</p>
    {comparison && comparison.status !== "assessable" && <p className="evidence-availability">{comparison.status === "comparison_only" ? "Comparison only · no calibrated alert policy" : "Comparison unavailable"}</p>}
    <div className="evidence-reading"><strong>{temp(hasComparison ? summary?.observed_f : estimate.temperature)}</strong>{hasComparison && <span>{Number.isFinite(summary?.departure_f) ? `${summary?.departure_f < 0 ? "−" : "+"}${Math.abs(summary?.departure_f).toFixed(1)}°` : "Unavailable"}<small>departure</small></span>}</div>
    {finding ? <Suspense fallback={<div className="chart-skeleton" role="status" aria-label="Loading temperature evidence"><span className="typing-indicator" aria-hidden="true"><i /><i /><i /></span></div>}><EvidenceChart finding={finding} /></Suspense> : <div className="comparison-summary">{hasComparison ? <><span>Expected <strong>{temp(summary?.expected_f)}</strong></span>{[summary?.typical_low_f, summary?.typical_high_f].every(Number.isFinite) && <span>Typical range <strong>{summary?.typical_low_f.toFixed(1)}–{summary?.typical_high_f.toFixed(1)}°F</strong></span>}</> : <span>{estimate.n_used ?? estimate.used_station_ids.length} usable stations</span>}</div>}
    <div className="evidence-section"><h3>{peers.length ? "Supporting readings" : estimate.status === "ok" ? "Used in the estimate" : "Available readings"}</h3><div className="peer-list">{peers.length ? peers.map((peer) => <div key={peer.station_id}><span>{nameOf(peer.station_id)}</span><strong>{temp(peer.observed_f)}</strong></div>) : estimate.used_station_ids.length ? estimate.used_station_ids.map((id) => <div key={id}><span>{nameOf(id)}</span><Icon name="check" size={13} /></div>) : <p className="microcopy">No usable station readings.</p>}</div></div>
    {hasComparison && <details className="disclosure evidence-explanation"><summary><Icon name="file" /><span>About this comparison</span><Icon name="chevron" size={12} /></summary><div className="disclosure-body"><p className="microcopy">Expected temperature uses the calibrated station comparison. The shaded band describes typical historical residuals, not a forecast or a guaranteed confidence interval.</p>{finding?.freshness === "stale" && <p className="microcopy">This finding is stale. Its readings describe the evidence time.</p>}<p className="microcopy">A departure is unusual behavior, not confirmed sensor failure. Check an independent reading.</p></div></details>}
  </div>;
}

export function EvidencePanel({ packet, record, overview, primaryLabel = "Temperature", sourceKind, finding, comparison, tab, onTab, mobile, onClose, closeRef, jsonRef, open }) {
  const [copyResult, setCopyResult] = useState(null);
  const json = JSON.stringify(record || packet, null, 2);
  const copyStatus = copyResult?.json === json ? copyResult.message : "";
  useEffect(() => {
    if (!copyResult) return;
    const timer = setTimeout(() => setCopyResult(null), copyResult.message === "Copied" ? 1800 : 5000);
    return () => clearTimeout(timer);
  }, [copyResult]);
  async function copy() {
    try { await navigator.clipboard.writeText(json); setCopyResult({ json, message: "Copied" }); }
    catch { setCopyResult({ json, message: "Copy unavailable. Use Download." }); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([json + "\n"], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `terra-${String(record?.message?.id || record?.orchard_id || packet?.query.location_id || "assessment").replace(/[^a-zA-Z0-9_-]/g, "-")}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function panelKeyDown(event) {
    if (event.key === "Escape") { event.preventDefault(); onClose(); }
    if (!mobile) return;
    if (event.key !== "Tab") return;
    const elements = [...event.currentTarget.querySelectorAll("button:not(:disabled), [tabindex='0'], summary")].filter((element) => {
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height || element.tabIndex < 0) return false;
      for (let parent = element.parentElement; parent && parent !== event.currentTarget; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS" && !parent.open && parent.querySelector(":scope > summary") !== element) return false;
      }
      return true;
    });
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  function tabKeyDown(event) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "temperature" : event.key === "End" ? "json" : tab === "json" ? "temperature" : "json";
    if (next === "json" && !record && !packet) return;
    onTab(next);
    event.currentTarget.parentElement.querySelector(`[data-tab="${next}"]`)?.focus();
  }
  return <aside className={`artifact-panel${record ? " record-evidence" : ""}`} data-open={open} inert={!open ? true : undefined} role={mobile ? "dialog" : undefined} aria-modal={mobile ? true : undefined} aria-label="Assessment evidence" onKeyDown={panelKeyDown}>
    <header className="artifact-header"><button ref={closeRef} className="icon-button" onClick={onClose} title={mobile ? "Back to field notes" : "Hide evidence"} aria-label={mobile ? "Back to field notes" : "Hide evidence"}><Icon name={mobile ? "back" : "close"} size={15} /></button><div className="artifact-tabs" role="tablist" aria-label="Evidence view">{[["temperature", primaryLabel], ["json", "JSON"]].map(([id, title]) => <button key={id} id={`tab-${id}`} data-tab={id} role="tab" disabled={id === "json" && !record && !packet} aria-selected={tab === id} aria-controls={`panel-${id}`} tabIndex={tab === id ? 0 : -1} onClick={() => onTab(id)} onKeyDown={tabKeyDown}>{title}</button>)}</div><div className="artifact-actions"><button className="icon-button" disabled={!record && !packet} onClick={copy} title={copyStatus === "Copied" ? "Copied" : "Copy JSON"} aria-label="Copy assessment JSON"><Icon name={copyStatus === "Copied" ? "check" : "copy"} size={15} /></button><button className="icon-button" disabled={!record && !packet} onClick={download} title="Download JSON" aria-label="Download assessment JSON"><Icon name="download" size={16} /></button></div></header>
    <div className="artifact-content" id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} key={tab}>
      {tab === "temperature" ? <>{packet && <TemperatureView packet={packet} sourceKind={sourceKind} finding={finding} comparison={comparison} />}{overview}</> : <><div className="json-caption">{record ? "Exact monitoring record" : "Exact assessment packet"}</div><pre ref={jsonRef} className="evidence-json" tabIndex={0} aria-label="Exact assessment packet">{json}</pre></>}
    </div><span className={`copy-feedback${copyStatus ? " visible" : ""}`} role="status">{copyStatus}</span>
  </aside>;
}
