"""Self-contained interactive chill-hour map (HTML + canvas, no external data)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from .grid import _outline


def _num(a: pd.Series, nd: int = 1) -> list[Any]:
    return [None if not np.isfinite(v) else round(float(v), nd) for v in a.to_numpy(dtype="float64")]


def map_payload(df: pd.DataFrame, stations: pd.DataFrame) -> dict[str, Any]:
    d = df[df["reason"] != "water"].reset_index(drop=True)
    reasons = sorted(set(d["reason"]) - {""})
    return {
        "meta": {k: (str(v) if not isinstance(v, int | float) else v) for k, v in df.attrs.items()},
        "dlat": float(df["dlat"].iloc[0]), "dlon": float(df["dlon"].iloc[0]),
        "lat": _num(d["lat"], 4), "lon": _num(d["lon"], 4),
        "chill": _num(d["chill_hours"], 0), "p10": _num(d["chill_p10"], 0), "p90": _num(d["chill_p90"], 0),
        "temp": _num(d["mean_temp_f"], 1), "elev": _num(d["elevation_ft"], 0),
        "near": _num(d["nearest_station_km"], 1), "nst": [int(v) for v in d["n_stations_within"]],
        "reason": [reasons.index(r) + 1 if r else 0 for r in d["reason"]], "reasons": reasons,
        "stations": [{"id": int(r.station_id), "name": str(r.name), "lat": round(float(r.lat), 4),
                      "lon": round(float(r.lon), 4), "elev": round(float(r.elevation_ft))}
                     for r in stations.dropna(subset=["lat", "lon"]).itertuples(index=False)],
        "outline": _outline(),
    }


TEMPLATE = r"""<title>Valley Chill Map</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,600&family=Source+Sans+3:wght@400;600&family=Source+Code+Pro:wght@400;500&display=swap">
<style>
/* Layout: header with the season's key figures, the map canvas, then a legend + cell readout row. */
:root {
  --bg: #f6f7f4; --surface: #ffffff; --fg: #1f2421; --muted: #5f6a64; --line: #d9dfda; --accent: #2e6b4f;
  --masked: #d4d8d3; --land: #eef1ec; --outline: #3a423d; --station: #ffffff; --station-edge: #1f2421;
  --display: "Source Serif 4", Georgia, serif; --sans: "Source Sans 3", system-ui, sans-serif;
  --mono: "Source Code Pro", ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #141816; --surface: #1b201d; --fg: #e4e9e5; --muted: #9aa69f; --line: #2d3530; --accent: #7cc4a0;
  --masked: #3a413c; --land: #20261f; --outline: #b9c4bd; --station: #141816; --station-edge: #e4e9e5; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #141816; --surface: #1b201d; --fg: #e4e9e5; --muted: #9aa69f; --line: #2d3530; --accent: #7cc4a0;
  --masked: #3a413c; --land: #20261f; --outline: #b9c4bd; --station: #141816; --station-edge: #e4e9e5; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font-family: var(--sans); font-size: 15px; line-height: 1.5; }
.wrap { max-width: 1100px; margin: 0 auto; padding-block: 28px 40px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 18px; }
h1 { font-family: var(--display); font-weight: 600; font-size: clamp(24px, 3.4vw, 34px); margin: 0; text-wrap: balance; }
.lede { color: var(--muted); margin: 4px 0 0; max-width: 70ch; }
.figs { display: flex; flex-wrap: wrap; gap: 10px 28px; font-variant-numeric: tabular-nums; }
.fig b { display: block; font-family: var(--mono); font-size: 20px; font-weight: 500; }
.fig span { color: var(--muted); font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; }
.controls button { font: 600 13px var(--sans); color: var(--fg); background: var(--surface); border: 1px solid var(--line);
  border-radius: 999px; padding: 6px 14px; cursor: pointer; }
.controls button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--bg); }
.controls button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mapbox { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
canvas { display: block; width: 100%; height: auto; touch-action: none; }
.row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px; }
@media (max-width: 720px) { .row { grid-template-columns: minmax(0, 1fr); } }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; min-width: 0; }
.panel h2 { font-size: 13px; letter-spacing: .05em; text-transform: uppercase; color: var(--muted); margin: 0 0 8px; font-weight: 600; }
#legend canvas { height: 14px; border-radius: 3px; }
.ticks { display: flex; justify-content: space-between; font-family: var(--mono); font-size: 12px; color: var(--muted); }
.key { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 13px; color: var(--muted); }
.sw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -1px; margin-right: 5px; }
dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 14px; margin: 0; font-variant-numeric: tabular-nums; }
dt { color: var(--muted); } dd { margin: 0; font-family: var(--mono); font-size: 14px; }
.note { color: var(--muted); font-size: 13px; margin: 0; max-width: 80ch; }
</style>
<div class="wrap">
  <header>
    <h1>Valley Chill Map</h1>
    <p class="lede" id="lede"></p>
  </header>
  <div class="figs" id="figs"></div>
  <div class="controls" role="group" aria-label="Map layer">
    <button type="button" data-layer="chill" aria-pressed="true">Chill hours</button>
    <button type="button" data-layer="spread" aria-pressed="false">Uncertainty (10&ndash;90% range)</button>
    <button type="button" data-layer="temp" aria-pressed="false">Mean temperature</button>
  </div>
  <div class="mapbox"><canvas id="map" width="1100" height="1000" aria-label="Map of estimated chill hours"></canvas></div>
  <div class="row">
    <section class="panel" id="legend"><h2 id="legend-title">Chill hours</h2>
      <canvas id="ramp" width="400" height="14"></canvas>
      <div class="ticks"><span id="t0"></span><span id="t1"></span><span id="t2"></span></div>
      <div class="key"><span><i class="sw" style="background:var(--masked)"></i>No estimate</span>
        <span><i class="sw" style="background:var(--station);border:1px solid var(--station-edge)"></i>Weather station</span></div>
    </section>
    <section class="panel"><h2>Cell</h2><dl id="cell"><dt>Point at a cell</dt><dd>&nbsp;</dd></dl></section>
  </div>
  <p class="note" id="method"></p>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  const D = JSON.parse(document.getElementById("data").textContent);
  const n = D.lat.length;
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  // viridis-like ramp (perceptual, colour-blind safe)
  const STOPS = [[68,1,84],[59,82,139],[33,145,140],[94,201,98],[253,231,37]];
  function ramp(t) { t = Math.max(0, Math.min(1, t)); const x = t * (STOPS.length - 1), i = Math.min(Math.floor(x), STOPS.length - 2), f = x - i;
    const a = STOPS[i], b = STOPS[i + 1]; return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`; }
  const spread = D.p10.map((v, i) => v == null || D.p90[i] == null ? null : D.p90[i] - v);
  const LAYERS = {
    chill: { title: "Chill hours (32–45 °F since Nov 1)", vals: D.chill, unit: "h" },
    spread: { title: "Uncertainty: width of the 10–90% chill-hour range", vals: spread, unit: "h" },
    temp: { title: "Mean temperature over the period", vals: D.temp, unit: "°F" },
  };
  function q(arr, p) { const v = arr.filter((x, i) => x != null && D.reason[i] === 0).sort((a, b) => a - b);
    return v.length ? v[Math.min(v.length - 1, Math.floor(p * (v.length - 1)))] : 0; }
  let layer = "chill";
  const fmt = (v, u) => v == null ? "–" : u === "h" || u === "ft" ? Math.round(v).toLocaleString() + " " + u
    : (Math.round(v * 10) / 10).toLocaleString() + " " + u;

  // header copy
  const m = D.meta, endDay = new Date(m.end); endDay.setUTCHours(endDay.getUTCHours() - 9);
  const endTxt = endDay.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  document.getElementById("lede").textContent = `Estimated chill hours from Nov 1 through ${endTxt}, season ${m.season}, on a ${m.km} km grid. Every cell is predicted from the weather stations around it.`;
  const valid = D.reason.filter(r => r === 0).length;
  const figs = [[q(D.chill, .5).toLocaleString() + " h", "median cell"], [q(D.chill, .1).toLocaleString() + "–" + q(D.chill, .9).toLocaleString() + " h", "10th–90th pct of cells"],
    [valid.toLocaleString(), "cells estimated"], [D.stations.length.toLocaleString(), "stations"]];
  document.getElementById("figs").innerHTML = figs.map(f => `<div class="fig"><b>${f[0]}</b><span>${f[1]}</span></div>`).join("");
  document.getElementById("method").textContent = `Model: ${m.model}${m.label ? " (" + m.label + ")" : ""}. Grey cells have fewer than 2 stations within 80 km or sit far above or below their nearest station, so no estimate is shown. Ground elevation from public terrain tiles; outline: US Census.`;

  // projection
  const cv = document.getElementById("map"), ctx = cv.getContext("2d");
  const lat0 = D.lat.reduce((a, b) => a + b, 0) / n, kx = Math.cos(lat0 * Math.PI / 180);
  const minLon = Math.min(...D.lon) - D.dlon, maxLon = Math.max(...D.lon) + D.dlon, minLat = Math.min(...D.lat) - D.dlat, maxLat = Math.max(...D.lat) + D.dlat;
  function size() { const w = cv.clientWidth || 1000, h = Math.round(w * (maxLat - minLat) / ((maxLon - minLon) * kx));
    const dpr = window.devicePixelRatio || 1; cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.height = h + "px"; return dpr; }
  let dpr = size();
  const X = lon => (lon - minLon) / (maxLon - minLon) * cv.width, Y = lat => (maxLat - lat) / (maxLat - minLat) * cv.height;
  function draw() {
    const L = LAYERS[layer], lo = q(L.vals, .02), hi = q(L.vals, .98);
    ctx.fillStyle = css("--land"); ctx.fillRect(0, 0, cv.width, cv.height);
    const w = Math.ceil(X(minLon + D.dlon) - X(minLon)) + 1, h = Math.ceil(Y(maxLat - D.dlat) - Y(maxLat)) + 1;
    for (let i = 0; i < n; i++) {
      const v = L.vals[i];
      ctx.fillStyle = D.reason[i] || v == null ? css("--masked") : ramp((v - lo) / ((hi - lo) || 1));
      ctx.fillRect(Math.floor(X(D.lon[i] - D.dlon / 2)), Math.floor(Y(D.lat[i] + D.dlat / 2)), w, h);
    }
    ctx.strokeStyle = css("--outline"); ctx.lineWidth = 1.2 * dpr;
    for (const ring of D.outline) { ctx.beginPath(); ring.forEach((p, k) => k ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))); ctx.stroke(); }
    for (const s of D.stations) { ctx.beginPath(); ctx.arc(X(s.lon), Y(s.lat), 3 * dpr, 0, 2 * Math.PI);
      ctx.fillStyle = css("--station"); ctx.fill(); ctx.strokeStyle = css("--station-edge"); ctx.lineWidth = 1 * dpr; ctx.stroke(); }
    // legend
    document.getElementById("legend-title").textContent = L.title;
    const r = document.getElementById("ramp"), rc = r.getContext("2d");
    for (let x = 0; x < r.width; x++) { rc.fillStyle = ramp(x / (r.width - 1)); rc.fillRect(x, 0, 1, r.height); }
    document.getElementById("t0").textContent = fmt(lo, L.unit); document.getElementById("t1").textContent = fmt((lo + hi) / 2, L.unit);
    document.getElementById("t2").textContent = fmt(hi, L.unit);
  }
  function cellAt(ev) {
    const b = cv.getBoundingClientRect(), px = (ev.clientX - b.left) * cv.width / b.width, py = (ev.clientY - b.top) * cv.height / b.height;
    const lon = minLon + px / cv.width * (maxLon - minLon), lat = maxLat - py / cv.height * (maxLat - minLat);
    let best = -1, bd = Infinity;
    for (let i = 0; i < n; i++) { const d = Math.abs(D.lat[i] - lat) / D.dlat + Math.abs(D.lon[i] - lon) / D.dlon; if (d < bd) { bd = d; best = i; } }
    return bd <= 1 ? best : -1;
  }
  function show(i) {
    const el = document.getElementById("cell");
    if (i < 0) { el.innerHTML = "<dt>Point at a cell</dt><dd>&nbsp;</dd>"; return; }
    const rows = [["Location", `${D.lat[i].toFixed(3)}, ${D.lon[i].toFixed(3)}`], ["Ground elevation", fmt(D.elev[i], "ft")]];
    if (D.reason[i]) rows.push(["Estimate", "none: " + D.reasons[D.reason[i] - 1]]);
    else rows.push(["Chill hours", fmt(D.chill[i], "h")], ["10–90% range", `${fmt(D.p10[i], "h")} – ${fmt(D.p90[i], "h")}`], ["Mean temperature", fmt(D.temp[i], "°F")]);
    rows.push(["Nearest station", fmt(D.near[i], "km")], ["Stations within 80 km", String(D.nst[i])]);
    el.innerHTML = rows.map(r => `<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join("");
  }
  cv.addEventListener("pointermove", ev => show(cellAt(ev)));
  cv.addEventListener("pointerdown", ev => show(cellAt(ev)));
  document.querySelectorAll(".controls button").forEach(b => b.addEventListener("click", () => {
    layer = b.dataset.layer; document.querySelectorAll(".controls button").forEach(x => x.setAttribute("aria-pressed", String(x === b))); draw(); }));
  window.addEventListener("resize", () => { dpr = size(); draw(); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  new MutationObserver(draw).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  draw();
})();
</script>
"""


def render_map_html(df: pd.DataFrame, stations: pd.DataFrame, label: str = "") -> str:
    """Page body (the artifact skeleton or ``wrap_document`` adds doctype/head)."""
    payload = map_payload(df, stations)
    payload["meta"]["label"] = label
    return TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))


def wrap_document(body: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            "</head><body style=\"margin:0\">" + body + "</body></html>")
