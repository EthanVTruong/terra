"""Self-contained HTML report for one logger: chill totals, data gaps, validation and the reference.

Answers the testing plan's usability question - "a new user can find the chill total and any data
gaps" - on one page with no server, no JavaScript and no external files.
"""

from __future__ import annotations

import html
import json
from typing import Any

import numpy as np
import pandas as pd

_CSS = """
body{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1e2421;background:#f7f8f6;margin:0}
main{max-width:980px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 10px}
.sub{color:#5f6b66;margin:0 0 18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card{background:#fff;border:1px solid #dfe3df;border-radius:10px;padding:14px 16px}
.card .k{color:#5f6b66;font-size:13px}.card .v{font-size:26px;font-weight:600;margin-top:2px}
.card .n{color:#5f6b66;font-size:12.5px;margin-top:4px}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #dfe3df;border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eef0ee;font-size:13.5px}
th{background:#f1f3f1;font-weight:600}
.ok{color:#3b6d11}.warn{color:#854f0b}.bad{color:#a32d2d}
.note{color:#5f6b66;font-size:13px}
svg{background:#fff;border:1px solid #dfe3df;border-radius:10px;width:100%;height:auto}
code{background:#eef0ee;padding:1px 4px;border-radius:4px}
"""


_ISSUE_TEXT = {
    "bad_checksum": "line corrupted in transit (checksum mismatch)",
    "bad_format": "line could not be parsed",
    "wrong_version": "firmware speaks a different record version",
    "unknown_node": "record from a logger not in the registry",
    "sensor_error": "the sensor reported a failed read",
    "out_of_range": "temperature outside the sensor's physical range",
    "rh_out_of_range": "humidity outside 0-100%",
    "duplicate": "same line received twice",
    "step": "implausible jump between consecutive readings",
    "stuck": "identical value for hours (sensor frozen)",
    "reboot": "logger restarted (power or reset)",
    "seq_gap": "samples lost between logger and computer",
    "clock_jump": "computer clock disagrees with the logger's uptime",
    "low_coverage": "too few valid samples in the hour",
    "no_samples": "nothing received for the hour",
    "residual_extreme": "one reading far from the reference (beyond 4 sigma)",
    "residual_persistent": "five or more hours in a row beyond 2.5 sigma",
    "reference_disagrees": "physics and ML references disagree; not judged",
    "no_reference": "no reference estimate for the hour",
}


def _esc(x: Any) -> str:
    return html.escape(str(x))


def _chart(h: pd.DataFrame, width: int = 940, height: int = 260) -> str:
    if h.empty or h["ts"].isna().all():
        return ""
    d = h.sort_values("ts")
    if len(d) > 1500:
        d = d.iloc[:: int(np.ceil(len(d) / 1500))]
    t = (d["ts"] - d["ts"].min()).dt.total_seconds().to_numpy()
    span = max(t.max(), 1.0)
    cols = [c for c in ("temp_f", "expected_f", "used_temp_f") if c in d]
    vals = np.concatenate([d[c].to_numpy(dtype="float64") for c in cols])
    if "expected_sd" in d:
        e, s = d["expected_f"].to_numpy(float), d["expected_sd"].to_numpy(float)
        vals = np.concatenate([vals, e - s, e + s])
    vals = vals[np.isfinite(vals)]
    if not len(vals):
        return ""
    step = 5.0 if vals.max() - vals.min() <= 30 else 10.0
    lo, hi = np.floor(vals.min() / step) * step, np.ceil(vals.max() / step) * step
    nt = int(round((hi - lo) / step))
    px, py, pw, ph = 44, 12, width - 56, height - 40

    def xy(tt: np.ndarray, v: np.ndarray) -> list[tuple[float, float]]:
        return [(px + pw * a / span, py + ph * (1 - (b - lo) / (hi - lo))) for a, b in zip(tt, v, strict=False)]

    def path(points: list[tuple[float, float]], ok: np.ndarray) -> str:
        out, pen = [], False
        for (x, y), good in zip(points, ok, strict=False):
            if not good:
                pen = False
                continue
            out.append(f"{'L' if pen else 'M'}{x:.1f} {y:.1f}")
            pen = True
        return " ".join(out)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Logger temperature against the fused reference">']
    for k in range(nt + 1):
        v = lo + step * k
        y = py + ph * (1 - k / nt)
        parts.append(f'<line x1="{px}" x2="{px + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eef0ee"/>'
                     f'<text x="{px - 6}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="#5f6b66">{v:.0f}&#176;F</text>')
    if "expected_sd" in d:
        e, s = d["expected_f"].to_numpy(float), d["expected_sd"].to_numpy(float)
        good = np.isfinite(e) & np.isfinite(s)
        if good.any():
            up = xy(t[good], (e + s)[good])
            dn = xy(t[good], (e - s)[good])[::-1]
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in up + dn)
            parts.append(f'<polygon points="{pts}" fill="#cfe3f6" opacity="0.7"/>')
        parts.append(f'<path d="{path(xy(t, e), np.isfinite(e))}" fill="none" stroke="#2f6e9e" stroke-width="1.2"/>')
    obs = d["temp_f"].to_numpy(float)
    okm = (d["qc_source"] == "ok").to_numpy()
    parts.append(f'<path d="{path(xy(t, obs), np.isfinite(obs) & okm)}" fill="none" stroke="#1e2421" stroke-width="1.3"/>')
    if "used_temp_f" in d:
        est = d["is_estimated"].fillna(False).to_numpy(bool)
        u = d["used_temp_f"].to_numpy(float)
        parts.append(f'<path d="{path(xy(t, u), est & np.isfinite(u))}" fill="none" stroke="#d85a30" stroke-width="1.6" stroke-dasharray="4 3"/>')
    bad = ~okm & np.isfinite(obs)
    for x, y in xy(t[bad], obs[bad]):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="#a32d2d"/>')
    parts.append(f'<text x="{px}" y="{height - 8}" font-size="11" fill="#5f6b66">{_esc(d["ts"].min().strftime("%b %d %H:%M"))} UTC</text>'
                 f'<text x="{px + pw}" y="{height - 8}" font-size="11" text-anchor="end" fill="#5f6b66">{_esc(d["ts"].max().strftime("%b %d %H:%M"))} UTC</text>')
    parts.append("</svg>")
    return "".join(parts)


def render(packet: dict[str, Any], hourly: pd.DataFrame | None = None) -> str:
    node, chill = packet["node"], packet["chill"]
    hrs, cp, ch = chill["hours"], chill["chill_portions"], chill["chill_hours"]
    ref = packet.get("reference")
    complete = chill.get("complete")

    def target_line(block: dict[str, Any], unit: str) -> str:
        if "target" not in block:
            return ""
        cls = "ok" if block["target_reached"] else "warn"
        word = "reached" if block["target_reached"] else f"{block['fraction_of_target'] * 100:.0f}% of"
        return f'<div class="n {cls}">{word} target {block["target"]:g} {unit}</div>'

    cards = f"""
<div class="cards">
 <div class="card"><div class="k">Chill Portions (Dynamic Model)</div><div class="v">{cp['with_estimates']:.1f}</div>
  <div class="n">range {cp['p10']:.1f}&ndash;{cp['p90']:.1f} &middot; measured only {cp['observed_only']:.1f}</div>{target_line(cp, 'CP')}</div>
 <div class="card"><div class="k">Chill hours (0&ndash;7.2 &#176;C)</div><div class="v">{ch['with_estimates']:.0f}</div>
  <div class="n">range {ch['p10']:.0f}&ndash;{ch['p90']:.0f} &middot; measured only {ch['observed_only']:.0f}</div>{target_line(ch, 'h')}</div>
 <div class="card"><div class="k">Hours measured</div><div class="v">{hrs['coverage_observed'] * 100:.1f}%</div>
  <div class="n">{hrs['observed_ok']} of {hrs['expected']} &middot; {hrs['estimated']} estimated &middot; {hrs['unfilled']} unfilled</div>
  <div class="n {'ok' if complete else 'warn'}">{'complete enough for conclusions (&#8805;95%)' if complete else 'below 95%: totals include estimates'}</div></div>
 <div class="card"><div class="k">Data gaps</div><div class="v">{len(chill['gaps'])}</div>
  <div class="n">{sum(g['hours'] for g in chill['gaps'])} hours missing, rejected or flagged</div></div>
</div>"""

    gap_rows = "".join(
        f"<tr><td>{_esc(g['start'][:16].replace('T', ' '))}</td><td>{_esc(g['end'][:16].replace('T', ' '))}</td>"
        f"<td>{g['hours']}</td><td>{_esc(g['status'])}</td></tr>" for g in chill["gaps"][:200])
    gaps_html = (f"<table><tr><th>From (UTC, hour-ending)</th><th>To</th><th>Hours</th><th>Status</th></tr>{gap_rows}</table>"
                 if gap_rows else '<p class="ok">No gaps: every hour in the period has a validated reading.</p>')

    si, hi = packet["issues"]["samples"], packet["issues"]["hours"]
    issues = "".join(f"<tr><td><code>{_esc(k)}</code></td><td>{_esc(_ISSUE_TEXT.get(k, ''))}</td>"
                     f"<td>{si.get(k, 0)}</td><td>{hi.get(k, 0)}</td></tr>" for k in sorted(set(si) | set(hi)))
    s = packet["samples"]
    stream = packet["stream"]
    val_html = f"""
<p>{s['received']} lines received, {s['decoded']} decoded, {s['rejected']} rejected before decoding.
{len(stream['reboots'])} reboot(s), {stream['lost_samples']} sample(s) lost in transit, {len(stream['clock_jumps'])} host clock jump(s).</p>
{f"<table><tr><th>Reason</th><th>Meaning</th><th>Samples</th><th>Hours</th></tr>{issues}</table>" if issues else '<p class="ok">No validation issues.</p>'}"""

    if ref:
        a = ref.get("agreement", {})
        ref_html = f"""
<p>Expected temperature at this logger from nearby reference stations: <b>{_esc(ref['method'])}</b>
(model <code>{_esc(ref['model_version'])}</code>). Each hour the physics estimate and the ML estimate are
combined with weights set by their uncertainty; the shaded band in the temperature chart is &plusmn;1&sigma; of the combination.</p>
<table>
<tr><th>Validated hours compared</th><td>{a.get('hours', 0)}</td></tr>
<tr><th>Orchard minus reference (median)</th><td>{a.get('median_difference_f', '&ndash;')} &#176;F ({a.get('median_difference_c', '&ndash;')} &#176;C);
 night {a.get('night_offset_f', '&ndash;')} &#176;F, day {a.get('day_offset_f', '&ndash;')} &#176;F</td></tr>
<tr><th>Spread of that difference (robust sd)</th><td>{a.get('robust_sd_f', '&ndash;')} &#176;F</td></tr>
<tr><th>Mean reference 1&sigma; / physics weight</th><td>{a.get('mean_expected_sd_f', '&ndash;')} &#176;F / {a.get('mean_physics_weight', '&ndash;')}</td></tr>
</table>
<p class="note">A steady orchard-minus-reference difference is expected (canopy vs grass-sited stations) and is learned,
not flagged. Hours are flagged when the logger departs from its own usual difference: one reading beyond 4&sigma;,
or five consecutive beyond 2.5&sigma;.</p>"""
    else:
        ref_html = '<p class="warn">No reference stations loaded: rules and coverage were checked, but nothing was estimated or cross-checked.</p>'

    chart = _chart(hourly) if hourly is not None else ""
    legend = ('<p class="note">Black: validated logger hours. Blue line and band: fused reference &plusmn;1&sigma;. '
              'Orange dashes: estimated hours used to fill gaps. Red dots: rejected or flagged readings.</p>') if chart else ""
    title = node.get("name") or node["node_id"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chill report &ndash; {_esc(title)}</title><style>{_CSS}</style></head><body><main>
<h1>Chill report: {_esc(title)}</h1>
<p class="sub">{_esc(chill['period']['first_local_date'])} to {_esc(chill['period']['last_local_date'])} (PST dates) &middot;
logger <code>{_esc(node['node_id'])}</code> &middot; {_esc(node.get('sensor', ''))}{(' in ' + _esc(node['shield']) + ' shield') if node.get('shield') else ''}</p>
{cards}
<h2>Temperature</h2>{chart}{legend}
<h2>Data gaps</h2>{gaps_html}
<h2>Validation</h2>{val_html}
<h2>Reference cross-check</h2>{ref_html}
<h2>Units</h2><p class="note">{'<br>'.join(_esc(v) for v in chill['units'].values())}<br>{_esc(chill['period']['timestamps'])}</p>
<details><summary class="note">Raw packet (JSON)</summary><pre class="note">{_esc(json.dumps(packet, indent=1, default=str))}</pre></details>
</main></body></html>"""
