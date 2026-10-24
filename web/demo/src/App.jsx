import { sourceFetch as fetch, sourceIdentity } from "../../src/source-fetch";
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowLeft, ArrowRight, ArrowUp, ArrowUpRight, BellSimple, CaretDown, CaretRight, Check, CircleNotch, Info, MapPin, MapTrifold, Minus, Plus, SidebarSimple, SpeakerHigh, SpeakerSlash, WarningCircle, X } from '@phosphor-icons/react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
const number = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '—';
const degrees = v => Number.isFinite(v) ? `${number(v)}°` : '—';
const signed = v => Number.isFinite(v) ? `${v >= 0 ? '+' : ''}${number(v)}°` : '—';
const clock = (v, date = false) => v ? new Intl.DateTimeFormat('en-US', {
  timeZone: 'Etc/GMT+8',
  ...(date ? {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  } : {
    hour: 'numeric',
    minute: '2-digit'
  })
}).format(new Date(v)) : '—';
const flagged = s => s?.flagged === true;
const support = p => p?.location_estimate?.n_used ?? 0;
// Count only the calibrated stations; test sensors also feed the estimate but are not stations.
const stationSupport = state => (state.assessment?.location_estimate?.used_station_ids || []).filter(id => state.stations.some(s => s.id === id)).length;
const supported = p => p?.location_estimate?.status === 'ok';
let audio;
function unlockAudio() {
  try {
    audio ??= new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume().catch(() => {});
  } catch {/*Sound is optional.*/}
}
function ping() {
  if (!audio || audio.state !== 'running') return;
  try {
    [740, 988].forEach((f, i) => {
      const o = audio.createOscillator(),
        g = audio.createGain(),
        t = audio.currentTime + i * .12;
      o.type = 'sine';
      o.frequency.value = f;
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(.055, t + .012);
      g.gain.exponentialRampToValueAtTime(.001, t + .36);
      o.connect(g);
      g.connect(audio.destination);
      o.start(t);
      o.stop(t + .38);
    });
  } catch {/*Visual feedback remains available.*/}
}
function useDemo() {
  const [state, setState] = useState(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const ref = useRef(null),
    seq = useRef(0),
    accepted = useRef(0),
    queue = useRef(Promise.resolve()),
    alive = useRef(true);
  const accept = useCallback((raw, id) => {
    const v = raw.state || raw;
    if (!Array.isArray(v.stations) || !Array.isArray(v.messages) || !v.assessment) throw new Error('The simulation returned incomplete evidence.');
    if (!alive.current || id < accepted.current) return;
    accepted.current = id;
    if (ref.current?.revision !== v.revision || ref.current?.run_id !== v.run_id) {
      ref.current = v;
      setState(v);
    }
    setError('');
  }, []);
  useEffect(() => {
    alive.current = true;
    let disposed = false,
      inFlight = false;
    async function load() {
      if (inFlight || disposed) return;
      inFlight = true;
      const id = ++seq.current;
      try {
        const r = await fetch('/api/demo/state'),
          v = await r.json();
        if (!r.ok) throw new Error(v.detail || 'The simulation is unavailable.');
        if (!disposed) accept(v, id);
      } catch (e) {
        if (!disposed) setError(e.message);
      } finally {
        inFlight = false;
      }
    }
    load();
    const interval = setInterval(load, 1200),
      visible = () => {
        if (document.visibilityState === 'visible') load();
      };
    document.addEventListener('visibilitychange', visible);
    return () => {
      disposed = true;
      alive.current = false;
      clearInterval(interval);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [accept]);
  const act = useCallback((action, endpoint = '/api/demo/action') => {
    unlockAudio();
    if (endpoint === '/api/demo/action' && action.type !== 'temperature') {
      window.dispatchEvent(new Event('terra-demo-command'));
    }
    const execute = async () => {
      if (alive.current) setBusy(true);
      const id = ++seq.current;
      try {
        const r = await fetch(endpoint, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json'
            },
            body: JSON.stringify(action)
          }),
          v = await r.json();
        if (!r.ok) throw new Error(typeof v.detail === 'string' ? v.detail : 'That change could not be applied.');
        accept(v, id);
        return v;
      } catch (e) {
        if (alive.current) setError(e.message);
        return null;
      } finally {
        if (alive.current) setBusy(false);
      }
    };
    queue.current = queue.current.then(execute, execute);
    return queue.current;
  }, [accept]);
  return {
    state,
    error,
    busy,
    act
  };
}
function Source({
  state
}) {
  return <details className="source-details"><summary aria-label="About this simulation"><Info size={15} /><span>Simulation</span><CaretDown size={11} /></summary><div className="source-popover"><strong>Real weather. Controlled changes.</strong><p>Saved CIMIS observations from March 2026. Temperature adjustments and reporting interruptions affect a separate copy.</p><dl><div><dt>Training ends</dt><dd>Aug 31, 2025</dd></div><div><dt>Calibration ends</dt><dd>Nov 30, 2025</dd></div><div><dt>Data</dt><dd>Mar 11, 2026 · held out</dd></div><div><dt>Detection</dt><dd>Frozen Terra reference</dd></div><div><dt>Heatmap</dt><dd>Nathan’s estimator · 60 ft</dd></div><div><dt>Phone</dt><dd>Simulated · no SMS sent</dd></div></dl><p className="source-footnote">Previously inspected historical data, outside training and calibration. The orchard pin is a demonstration location.</p><code>{state?.source?.sha256?.slice(0, 16)}</code></div></details>;
}
function PageNav({
  view,
  state
}) {
  return <nav className="page-nav" aria-label="Simulation apps"><Source state={state} /><div className="app-links"><a href="/stations" className={view === 'stations' ? 'current' : ''}>Station lab</a><span>/</span><a href="/terra" className={view === 'terra' ? 'current' : ''}>Terra</a><a className="open-other" href={view === 'stations' ? '/terra' : '/stations'} target="_blank" rel="noreferrer" title="Open the other app in a separate tab" aria-label="Open the other app in a separate tab"><ArrowUpRight size={14} /></a></div></nav>;
}
function Notification({
  notification,
  onClose
}) {
  return <aside className="phone-notification" role="status" aria-label="Simulated phone notification"><div className="notification-heading"><span><BellSimple size={14} weight="fill" />terra</span><span>Simulated phone · now</span><button className="icon-button" onClick={onClose} aria-label="Dismiss notification"><X size={13} /></button></div><p>{notification.text}</p><a href={`/terra?note=${encodeURIComponent(notification.id)}`} target="_blank" rel="noreferrer">Open in Terra <ArrowUpRight size={13} /></a></aside>;
}
function usePhone(state, view) {
  const [notice, setNotice] = useState(null),
    [sound, setSound] = useState(() => {
      try {
        return localStorage.getItem('terra-demo-sound') !== 'off';
      } catch {
        return true;
      }
    }),
    seen = useRef(null),
    run = useRef(null);
  useEffect(() => {
    if (!state) return;
    const list = state.notifications || [];
    if (run.current !== state.run_id) {
      run.current = state.run_id;
      seen.current = new Set(list.map(n => n.id));
      setNotice(null);
      return;
    }
    const fresh = list.filter(n => !seen.current.has(n.id));
    list.forEach(n => seen.current.add(n.id));
    if (fresh.length) {
      const last = fresh.at(-1);
      setNotice(last);
      if (view === 'stations' && sound) {
        const key = `terra-demo-ping:${state.run_id}:${last.id}`;
        try {
          if (!localStorage.getItem(key)) {
            localStorage.setItem(key, 'played');
            ping();
          }
        } catch {
          ping();
        }
      }
    }
  }, [state, sound, view]);
  const toggleSound = () => {
    unlockAudio();
    setSound(v => {
      try {
        localStorage.setItem('terra-demo-sound', v ? 'off' : 'on');
      } catch {/*Session-only preference.*/}
      return !v;
    });
  };
  return {
    notice,
    setNotice,
    sound,
    toggleSound
  };
}
function StationLabel({
  station
}) {
  if (!station.online) return <span className="station-state offline">Offline</span>;
  if (flagged(station)) return <span className="station-state flagged"><WarningCircle size={12} />Flagged</span>;
  if (station.assessment?.available === false) return <span className="station-state unavailable">Unassessed</span>;
  return <span className="station-state">Usable</span>;
}
const mercatorY = lat => (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2;
function position(lat, lon, b) {
  return {
    left: `${(lon - b.west) / (b.east - b.west) * 100}%`,
    top: `${(mercatorY(lat) - mercatorY(b.north)) / (mercatorY(b.south) - mercatorY(b.north)) * 100}%`
  };
}
// Same viridis-like ramp as the ChillWatch Valley Chill Map (perceptual, colour-blind safe).
const palette = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
function heatColor(v, lo, hi) {
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1))),
    x = t * (palette.length - 1),
    i = Math.min(Math.floor(x), palette.length - 2),
    f = x - i;
  return palette[i].map((n, j) => Math.round(n + (palette[i + 1][j] - n) * f));
}
// Stretch the ramp to the shown layer's values, as each chill-map layer does.
function heatRange(cells) {
  const values = cells.filter(c => Number.isFinite(c.temperature_f) && c.status === 'ok').map(c => c.temperature_f);
  if (!values.length) return [35, 125];
  const lo = Math.floor(Math.min(...values)),
    hi = Math.ceil(Math.max(...values));
  return hi - lo < 4 ? [lo - 2, hi + 2] : [lo, hi];
}
function HeatField({
  cells,
  lo,
  hi
}) {
  const ref = useRef(null);
  useEffect(() => {
    const target = ref.current;
    if (!target) return;
    const lats = [...new Set(cells.map(c => c.lat))].sort((a, b) => b - a),
      lons = [...new Set(cells.map(c => c.lon))].sort((a, b) => a - b);
    target.width = 600;
    target.height = 600;
    const ctx = target.getContext('2d');
    ctx.clearRect(0, 0, 600, 600);
    if (!lats.length || !lons.length) return;
    const small = document.createElement('canvas');
    small.width = lons.length;
    small.height = lats.length;
    const sc = small.getContext('2d'),
      pixels = sc.createImageData(small.width, small.height);
    cells.forEach(c => {
      const i = (lats.indexOf(c.lat) * lons.length + lons.indexOf(c.lon)) * 4,
        v = c.temperature_f;
      if (Number.isFinite(v) && c.status === 'ok') {
        const color = heatColor(v, lo, hi);
        pixels.data[i] = color[0];
        pixels.data[i + 1] = color[1];
        pixels.data[i + 2] = color[2];
        pixels.data[i + 3] = 255;
      }
    });
    sc.putImageData(pixels, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(small, 0, 0, 600, 600);
  }, [cells, lo, hi]);
  return <canvas ref={ref} className="heat-field" aria-hidden="true" />;
}
// Esri Light Gray Canvas: plain base, with place names as a separate layer drawn above the heatmap.
const BASEMAP_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_';
function BaseMap({
  bounds: b,
  width,
  layer = 'Base'
}) {
  // Pick the tile level whose 256px tiles best match the map's on-screen width.
  const z = Math.max(8, Math.min(16, Math.round(Math.log2(width * 360 / (256 * (b.east - b.west)))))),
    n = 2 ** z,
    left = (b.west + 180) / 360 * n,
    right = (b.east + 180) / 360 * n,
    top = mercatorY(b.north) * n,
    bottom = mercatorY(b.south) * n,
    tiles = [];
  for (let x = Math.floor(left); x < Math.ceil(right); x++) for (let y = Math.floor(top); y < Math.ceil(bottom); y++) tiles.push({
    x,
    y,
    left: (x - left) / (right - left) * 100,
    top: (y - top) / (bottom - top) * 100,
    width: 100 / (right - left),
    height: 100 / (bottom - top)
  });
  return <div className={layer === 'Base' ? 'basemap' : 'basemap-labels'} aria-hidden="true">{tiles.map(t => <img key={`${t.x}-${t.y}`} alt="" draggable="false" src={`${BASEMAP_TILES}${layer}/MapServer/tile/${z}/${t.y}/${t.x}`} style={{
      left: `${t.left}%`,
      top: `${t.top}%`,
      width: `${t.width}%`,
      height: `${t.height}%`
    }} />)}</div>;
}
const latitude = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI;
// About tile level 16, the deepest level of the light-gray basemap, at desktop width.
const MAX_ZOOM = 64;
const domainSpan = bounds => [(bounds.east - bounds.west) / 360, mercatorY(bounds.south) - mercatorY(bounds.north)];
// View size in Web Mercator units. Zoom 1 covers the estimated field exactly along one axis.
function viewSpan(bounds, aspect, zoom) {
  let [dx, dy] = domainSpan(bounds);
  if (dx / dy > aspect) dx = dy * aspect;else dy = dx / aspect;
  return [dx / zoom, dy / zoom];
}
// The view never leaves the estimated field, so the heatmap always fills the map.
function clampPan(bounds, span, [x, y]) {
  const [fx, fy] = domainSpan(bounds),
    limitX = Math.max(0, (fx - span[0]) / 2),
    limitY = Math.max(0, (fy - span[1]) / 2);
  return [Math.max(-limitX, Math.min(limitX, x)), Math.max(-limitY, Math.min(limitY, y))];
}
function fitMapBounds(bounds, span, pan) {
  const cx = (bounds.west + bounds.east) / 720 + .5 + pan[0],
    cy = (mercatorY(bounds.north) + mercatorY(bounds.south)) / 2 + pan[1],
    [dx, dy] = span;
  return {
    west: (cx - dx / 2 - .5) * 360,
    east: (cx + dx / 2 - .5) * 360,
    north: latitude(cy - dy / 2),
    south: latitude(cy + dy / 2)
  };
}
function pointAt(b, fx, fy) {
  return {
    lat: latitude(mercatorY(b.north) + fy * (mercatorY(b.south) - mercatorY(b.north))),
    lon: b.west + fx * (b.east - b.west)
  };
}
const MAX_SPOTS = 6;
function spotValue(reading, layer) {
  const estimate = reading?.layers?.[layer];
  return estimate?.status === 'ok' ? degrees(estimate.temperature_f) : estimate ? 'No estimate' : '…';
}
// Map spots that are not stations: read-only estimates from the same estimator as the heatmap.
function useSpotReadings(spots, fingerprint) {
  const [readings, setReadings] = useState({});
  useEffect(() => {
    let disposed = false;
    spots.forEach(spot => fetch('/api/demo/point', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        lat: spot.lat,
        lon: spot.lon
      })
    }).then(r => r.ok ? r.json() : null).then(v => {
      if (!disposed) setReadings(current => ({
        ...current,
        [spot.id]: v
      }));
    }).catch(() => {}));
    return () => {
      disposed = true;
    };
  }, [spots, fingerprint]);
  return readings;
}
function WeatherMap({
  state,
  selected,
  onSelect,
  layer,
  spots,
  readings,
  onAddSpot
}) {
  const ref = useRef(null),
    drag = useRef(null),
    dragged = useRef(false),
    [size, setSize] = useState({
      width: 900,
      height: 500
    }),
    [view, setView] = useState({
      zoom: 1,
      pan: [0, 0]
    }),
    domain = state.heatmap.bounds,
    aspect = size.width / size.height,
    span = viewSpan(domain, aspect, view.zoom),
    pan = clampPan(domain, span, view.pan),
    b = fitMapBounds(domain, span, pan),
    cells = state.heatmap.layers?.[layer] || state.heatmap.cells || [],
    [lo, hi] = heatRange(cells),
    available = cells.some(c => Number.isFinite(c.temperature_f) && c.status === 'ok');
  function pointerDown(e) {
    if (e.button !== 0) return;
    drag.current = {
      x: e.clientX,
      y: e.clientY,
      pan,
      moved: false
    };
  }
  function pointerMove(e) {
    const start = drag.current;
    if (!start) return;
    const dx = e.clientX - start.x,
      dy = e.clientY - start.y;
    if (!start.moved && Math.hypot(dx, dy) < 4) return;
    if (!start.moved) ref.current.setPointerCapture(e.pointerId);
    start.moved = true;
    const box = ref.current.getBoundingClientRect();
    setView(v => ({
      ...v,
      pan: clampPan(domain, span, [start.pan[0] - dx / box.width * span[0], start.pan[1] - dy / box.height * span[1]])
    }));
  }
  // Zoom around a point given as a fraction of the map box, keeping that spot under the cursor.
  const zoomBy = useCallback((factor, fx = .5, fy = .5) => setView(v => {
    const zoom = Math.max(1, Math.min(MAX_ZOOM, v.zoom * factor)),
      before = viewSpan(domain, aspect, v.zoom),
      after = viewSpan(domain, aspect, zoom),
      start = clampPan(domain, before, v.pan);
    return {
      zoom,
      pan: clampPan(domain, after, [start[0] + (fx - .5) * (before[0] - after[0]), start[1] + (fy - .5) * (before[1] - after[1])])
    };
  }), [domain.north, domain.south, domain.east, domain.west, aspect]);
  useEffect(() => {
    const target = ref.current;
    if (!target) return;
    // Non-passive so the wheel zooms the map instead of scrolling the page.
    const wheel = e => {
      e.preventDefault();
      const box = target.getBoundingClientRect();
      zoomBy(Math.exp(-e.deltaY * .0015), (e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height);
    };
    target.addEventListener('wheel', wheel, {
      passive: false
    });
    return () => target.removeEventListener('wheel', wheel);
  }, [zoomBy]);
  function pointerUp() {
    dragged.current = Boolean(drag.current?.moved);
    drag.current = null;
  }
  function toggleSpot(e) {
    if (e.target.closest('button, a, .map-legend')) return;
    const box = ref.current.getBoundingClientRect(),
      {
        lat,
        lon
      } = pointAt(b, (e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height);
    if (lat < domain.south || lat > domain.north || lon < domain.west || lon > domain.east) return;
    onAddSpot(lat, lon);
  }
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const box = entries[0].contentRect;
      if (box.height) setSize({
        width: box.width,
        height: box.height
      });
    });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const corner = position(domain.north, domain.west, b),
    opposite = position(domain.south, domain.east, b);
  const fieldStyle = {
    left: corner.left,
    top: corner.top,
    width: `${parseFloat(opposite.left) - parseFloat(corner.left)}%`,
    height: `${parseFloat(opposite.top) - parseFloat(corner.top)}%`
  };
  return <div className="weather-map" ref={ref} role="group" aria-label={`${layer === 'raw' ? 'Raw' : 'Terra filtered'} estimated temperature heatmap. Drag to move, scroll to zoom, click to check a spot.`} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} onClickCapture={e => {
      if (dragged.current) {
        e.stopPropagation();
        dragged.current = false;
      }
    }} onClick={toggleSpot}><BaseMap bounds={b} width={size.width} /><div className="heat-domain" style={fieldStyle}><HeatField cells={cells} lo={lo} hi={hi} /></div><BaseMap bounds={b} width={size.width} layer="Reference" /><div className="map-region">SACRAMENTO VALLEY</div>{state.stations.map(station => {
      const pos = position(station.lat, station.lon, b),
        right = parseFloat(pos.left) > 70,
        bottom = parseFloat(pos.top) > 82;
      return <button key={station.id} className={`map-station${selected === station.id ? ' selected' : ''}${!station.online ? ' is-offline' : flagged(station) ? ' is-flagged' : ''}${right ? ' caption-left' : ''}${bottom ? ' caption-up' : ''}`} style={pos} onClick={() => onSelect(station.id)} aria-label={`Select ${station.name}, ${station.online ? degrees(station.value_f) + ' Fahrenheit' : 'offline'}`} aria-pressed={selected === station.id}><span className="map-point" /><span className="map-station-caption"><strong>{station.name}</strong><span>{station.online ? degrees(station.value_f) : 'Offline'}{station.online && flagged(station) && <WarningCircle size={12} />}</span>{layer === 'filtered' && state.assessment.estimator_qc_exclusions.some(row => row.station_id === station.id) && <small>Excluded from field</small>}</span></button>;
    })}{spots.map(spot => {
      const value = spotValue(readings[spot.id], layer);
      return <button key={spot.id} className={`map-spot${selected === spot.id ? ' selected' : ''}`} style={position(spot.lat, spot.lon, b)} onClick={() => onSelect(spot.id)} aria-label={`Select map spot, ${value}`} aria-pressed={selected === spot.id}><span className="map-spot-point" /><span className="map-spot-caption"><strong>Map spot</strong>{value}</span></button>;
    })}{(state.sensors || []).map(sensor => {
      const flaggedSensor = sensor.check && sensor.check.level !== 'none';
      return <button key={sensor.id} className={`map-spot is-sensor${flaggedSensor ? ' is-flagged' : ''}${selected === sensor.id ? ' selected' : ''}`} style={position(sensor.lat, sensor.lon, b)} onClick={() => onSelect(sensor.id)} aria-label={`Select test sensor, ${degrees(sensor.value_f)} Fahrenheit${flaggedSensor ? ', flagged' : ''}`} aria-pressed={selected === sensor.id}><span className="map-spot-point" /><span className="map-spot-caption"><strong>Test sensor</strong><span>{degrees(sensor.value_f)}{flaggedSensor && <WarningCircle size={12} />}</span></span></button>;
    })}<div className="map-zoom" role="group" aria-label="Map zoom"><button onClick={() => zoomBy(2)} disabled={view.zoom >= MAX_ZOOM} aria-label="Zoom in" title="Zoom in"><Plus size={14} /></button><button onClick={() => zoomBy(1 / 2)} disabled={view.zoom <= 1} aria-label="Zoom out" title="Zoom out"><Minus size={14} /></button>{(view.zoom > 1 || pan[0] !== 0 || pan[1] !== 0) && <button onClick={() => setView({
          zoom: 1,
          pan: [0, 0]
        })} aria-label="Reset map view" title="Reset view"><ArrowClockwise size={13} /></button>}</div><div className="orchard-pin" style={position(state.orchard.lat, state.orchard.lon, b)}><MapPin size={22} weight="fill" /><span>Orchard</span></div>{!available && <div className="map-no-support"><WarningCircle size={18} /><strong>No supported temperature surface</strong><span>Not enough usable station readings</span></div>}<div className="map-legend"><strong>Temperature (°F)</strong><div>{Array.from({
          length: 32
        }, (_, i) => <i key={i} style={{
          background: `rgb(${heatColor(i / 31, 0, 1).join(',')})`
        }} />)}</div><p><span>{lo}°</span><span>{Math.round((lo + hi) / 2)}°</span><span>{hi}°</span></p></div><div className="map-attribution" title="Powered by Esri · Esri, HERE, Garmin, © OpenStreetMap contributors">© <a href="https://www.esri.com" target="_blank" rel="noreferrer">Esri</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a></div></div>;
}
function Comparison({
  state,
  stationId,
  cutoff,
  history
}) {
  const data = (history || state.history || []).filter(r => r.station_id === stationId && (!cutoff || Date.parse(r.timestamp) <= Date.parse(cutoff))).map(r => ({
    time: r.timestamp,
    observed: r.online ? r.value_f : null,
    expected: r.expected_f
  }));
  return <div className="comparison"><div className="chart-key"><span><i />Observed</span><span><i className="expected" />Expected</span></div><div className="chart-box"><ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{
          top: 10,
          right: 4,
          bottom: 0,
          left: -25
        }}><CartesianGrid vertical={false} stroke="#e7e7e7" strokeDasharray="2 4" /><XAxis dataKey="time" tickFormatter={v => clock(v).replace(':00', '')} minTickGap={40} tick={{
            fontSize: 10,
            fill: '#858585'
          }} axisLine={false} tickLine={false} /><YAxis domain={[20, 130]} ticks={[30, 70, 110]} tickFormatter={v => `${v}°`} tick={{
            fontSize: 10,
            fill: '#858585'
          }} axisLine={false} tickLine={false} /><Tooltip labelFormatter={v => `${clock(v)} PST`} formatter={(v, n) => [`${number(v)}°F`, n === 'observed' ? 'Observed' : 'Expected']} contentStyle={{
            fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
            fontSize: 12,
            border: '1px solid #e4e4e4',
            borderRadius: 8
          }} /><Line dataKey="expected" type="linear" stroke="#8b9b82" strokeWidth={1.6} dot={false} isAnimationActive={false} /><Line dataKey="observed" type="linear" stroke="#292929" strokeWidth={1.7} dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div></div>;
}
const weatherVariables = [
  ['relative_humidity_pct', 'Humidity', '%', 100],
  ['wind_speed_mph', 'Average wind', 'mph', 150],
  ['solar_radiation_w_m2', 'Solar radiation', 'W/m²', 2000],
];
function VariableControl({ station, field, label, unit, max, busy, act }) {
  const reading = station.weather?.[field];
  const [value, setValue] = useState(String(reading?.observed ?? ''));
  const [quality, setQuality] = useState(reading?.source_flag || '');
  useEffect(() => { setValue(String(reading?.observed ?? '')); setQuality(reading?.source_flag || ''); }, [station.id, reading?.observed, reading?.source_flag]);
  const valid = value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= max;
  const flagged = reading?.classification === 'suspected_discrepancy';
  return <form className="weather-row" onSubmit={event => { event.preventDefault(); if (valid) act({type:'weather',station_id:station.id,variable:field,value:Number(value),source_flag:quality}); }}>
    <div className="weather-row-head"><label htmlFor={`weather-${field}`}>{label} <span>{unit}</span></label><span className="weather-row-links"><button type="button" disabled={busy || !station.online} onClick={()=>act({type:'weather',station_id:station.id,variable:field,value:null,source_flag:''})}>Missing</button><button type="button" disabled={busy} onClick={()=>act({type:'weather_reset',station_id:station.id,variable:field})}>Recorded</button></span></div>
    <div className="weather-row-inputs">
      <input id={`weather-${field}`} aria-label={`${station.name} ${label}`} type="number" min="0" max={max} step="0.1" value={value} onChange={e=>setValue(e.target.value)} disabled={busy || !station.online} />
      <select aria-label={`${label} source quality`} value={quality} onChange={e=>setQuality(e.target.value)} disabled={busy || !station.online}><option value="">Usable</option><option value="Y">Source warning</option>{quality && quality !== 'Y' && <option value={quality}>Source flag {quality}</option>}</select>
      <button className="quiet-button" type="submit" disabled={busy || !station.online || !valid}>Apply</button>
    </div>
    <p className={`weather-row-status${flagged ? ' is-flagged-text' : ''}`} role="status">{flagged ? 'Station discrepancy' : reading?.status === 'assessable' ? reading?.level === 'moderate' ? 'Departure · awaiting sustained readings' : 'Within comparison policy' : reading?.status === 'low_daylight' ? 'Solar comparisons resume in daylight' : (reading?.reason || 'Unavailable').replaceAll('_',' ')}{reading?.expected != null ? ` · Peers ${number(reading.expected)} ${unit}` : ''}</p>
  </form>;
}
// A test sensor's humidity/wind/solar reading, judged by the same peer-discrepancy policy as stations.
function SensorWeatherControl({ sensor, field, label, unit, max, busy, act }) {
  const set = sensor.weather?.[field],
    reading = sensor.weather_checks?.[field],
    // Unset readings start at the value interpolated from nearby stations.
    expected = sensor.weather_expected?.[field],
    start = set ?? (expected == null ? null : Math.round(expected * 10) / 10),
    [value, setValue] = useState(start == null ? '' : String(start));
  useEffect(() => { setValue(start == null ? '' : String(start)); }, [sensor.id, start]);
  const valid = value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= max;
  const status = set == null ? `Not reported${expected != null ? ` · Nearby stations ${number(expected)} ${unit}` : ''}` : reading?.classification === 'suspected_discrepancy' ? 'Flagged · far from nearby stations'
    : reading?.status === 'assessable' ? reading.level === 'moderate' ? 'Departure · awaiting sustained readings' : 'Within comparison policy'
    : reading?.status === 'low_daylight' ? 'Solar comparisons resume in daylight' : (reading?.reason || 'Unavailable').replaceAll('_', ' ');
  return <form onSubmit={event => { event.preventDefault(); if (valid) act({type: 'sensor_weather', sensor_id: sensor.id, variable: field, value: Number(value)}); }}>
    <label htmlFor={`sensor-weather-${field}`}>{label} <span>{unit}</span></label>
    <div style={{display: 'flex', gap: 6, margin: '6px 0'}}>
      <input id={`sensor-weather-${field}`} aria-label={`Test sensor ${label}`} type="number" min="0" max={max} step="0.1" placeholder="Not reported" value={value} onChange={e => setValue(e.target.value)} disabled={busy} style={{minWidth: 0, flex: 1}} />
      <button className="quiet-button" type="submit" disabled={busy || !valid}>Apply</button>
      <button className="quiet-button" type="button" disabled={busy || set == null} onClick={() => act({type: 'sensor_weather', sensor_id: sensor.id, variable: field, value: null})}>Clear</button>
    </div>
    <p className={`small-note${reading?.classification === 'suspected_discrepancy' && set != null ? ' is-flagged-text' : ''}`} role="status">{status}{set != null && reading?.expected != null ? ` · Peers ${number(reading.expected)} ${unit}` : ''}</p>
  </form>;
}
// One station's replay hours, oldest first, with chill hours (32–45°F) accumulated on top of the
// real season-to-date total before the replay when that CIMIS history is available.
function stationHistory(state, stationId) {
  let total = state.stations.find(s => s.id === stationId)?.season_chill?.before_replay ?? 0;
  return (state.history || []).filter(r => r.station_id === stationId).sort((a, b) => a.timestamp.localeCompare(b.timestamp)).map(r => {
    if (r.online && Number.isFinite(r.value_f) && r.value_f >= 32 && r.value_f <= 45) total += 1;
    return {...r, chill_hours: total, hour: clock(r.timestamp)};
  });
}
function HistoryChart({ title, rows, lines, unit, onClose }) {
  return <section className="weather-panel" aria-label={title}><button className="weather-back" onClick={onClose}><ArrowRight size={12} className="flip" />{title}</button><div className="history-chart"><ResponsiveContainer width="100%" height={170}><LineChart data={rows} margin={{top: 8, right: 6, bottom: 0, left: -18}}><CartesianGrid stroke="#ecede8" vertical={false} /><XAxis dataKey="hour" tick={{fontSize: 9, fill: '#8a9086'}} interval="preserveStartEnd" minTickGap={24} /><YAxis tick={{fontSize: 9, fill: '#8a9086'}} domain={['auto', 'auto']} allowDecimals={false} /><Tooltip formatter={(value, name) => [Number.isFinite(value) ? `${Math.round(value * 10) / 10}${unit}` : '—', name]} labelStyle={{fontSize: 11}} contentStyle={{fontSize: 11}} />{lines.map(([key, name, color]) => <Line key={key} type="monotone" dataKey={key} name={name} stroke={color} strokeWidth={1.8} dot={false} isAnimationActive={false} connectNulls />)}</LineChart></ResponsiveContainer><div className="history-legend">{lines.map(([key, name, color]) => <span key={key}><i style={{background: color}} />{name}</span>)}</div></div></section>;
}
// Takes the station list's place in the panel while open, so the controls need no scrolling.
function WeatherControls({station,busy,act,onClose}) {
  return <section className="weather-panel" aria-label={`${station.name} humidity, wind and solar`}><button className="weather-back" onClick={onClose}><ArrowRight size={12} className="flip" />Humidity, wind & solar</button><div className="weather-rows">
    {weatherVariables.map(([field,label,unit,max])=><VariableControl key={`${station.id}:${field}`} station={station} field={field} label={label} unit={unit} max={max} busy={busy} act={act}/>)}
  </div></section>;
}
function StationControl({
  state,
  selected,
  setSelected,
  act,
  busy,
  layer
}) {
  const s = state.stations.find(s => s.id === selected) || state.stations[0],
    [draft, setDraft] = useState(String(s.value_f ?? 50)),
    timer = useRef(null),
    panel = useRef(null),
    // 'list' shows station reporting; 'weather', 'temp' and 'chill' take its place.
    [view, setView] = useState('list'),
    latestStation = useRef(s);
  latestStation.current = s;
  const pending = s.online && String(draft).trim() !== '' && Number(draft) !== s.value_f,
    rows = stationHistory(state, s.id),
    chill = rows.length ? rows.at(-1).chill_hours : 0,
    season = s.season_chill,
    // Season view: Nov 1 to the replay day by day, then the replay hours.
    chillRows = season ? [...season.daily.map(d => ({hour: d.date.slice(5), chill_hours: d.chill_hours})), ...rows] : rows,
    close = () => setView('list');
  useEffect(() => {
    if (view === 'list') return;
    // A click anywhere outside the panel returns it to the station list.
    const away = e => {
      if (panel.current && !panel.current.contains(e.target)) setView('list');
    };
    document.addEventListener('pointerdown', away);
    return () => document.removeEventListener('pointerdown', away);
  }, [view]);
  useEffect(() => {
    setDraft(String(s.value_f ?? s.original_f ?? 50));
  }, [s.id, s.value_f, s.original_f, state.run_id]);
  useEffect(() => {
    const cancel = () => {
      clearTimeout(timer.current);
      setDraft(String(latestStation.current.value_f ?? latestStation.current.original_f ?? 50));
    };
    window.addEventListener('terra-demo-command', cancel);
    return () => {
      cancel();
      window.removeEventListener('terra-demo-command', cancel);
    };
  }, []);
  function change(v) {
    const n = Number(v);
    setDraft(v);
    clearTimeout(timer.current);
    if (String(v).trim() && Number.isFinite(n) && n >= -10 && n <= 125) {
      const id = s.id;
      timer.current = setTimeout(() => act({
        type: 'temperature',
        station_id: id,
        value_f: n
      }), 350);
    }
  }
  return <aside className="station-controls" ref={panel} aria-label="Selected station controls"><div className="selected-station-heading"><h2>{s.name}</h2>{pending ? <span className="station-state">Checking…</span> : <StationLabel station={s} />}</div>{view !== 'weather' && <><div className="reading-cell"><label htmlFor="temperature-value">Temperature °F</label><div className="temperature-value"><input id="temperature-value" aria-label={`${s.name} temperature Fahrenheit`} type="number" min="-10" max="125" step="0.1" value={draft} disabled={!s.online || busy} onBlur={() => { if (!String(draft).trim()) setDraft(String(s.value_f ?? s.original_f)); }} onChange={e => change(e.target.value)} /><span>°</span></div></div><input className="temperature-slider" aria-label={`Adjust ${s.name} temperature`} type="range" min="-10" max="125" step="0.1" value={draft} disabled={!s.online || busy} onChange={e => change(e.target.value)} /><div className="reading-meta"><button className="reading-meta-link" onClick={() => setView(view === 'temp' ? 'list' : 'temp')} aria-pressed={view === 'temp'} title="Show expected and recorded temperature over time">Expected <strong>{pending ? '—' : degrees(s.assessment.expected_f)}</strong></button><span>Difference <strong>{pending ? '—' : signed(s.assessment.residual_f)}</strong></span></div>{s.override !== null && s.override !== undefined && <div className="slider-context"><span /><button disabled={busy} onClick={() => {
          clearTimeout(timer.current);
          act({
            type: 'temperature',
            station_id: s.id,
            value_f: s.original_f
          });
        }}><ArrowClockwise size={12} />Restore {degrees(s.original_f)}</button></div>}<button className="metric-tile" onClick={() => setView(view === 'chill' ? 'list' : 'chill')} aria-pressed={view === 'chill'}><span>Chill hours <small>· {season ? 'since Nov 1' : 'this replay'}</small></span><strong>{chill} h</strong><CaretRight size={12} /></button><div className="reading-state" aria-live="polite">{!s.online ? 'Reading withheld from the simulation.' : pending ? 'Evaluating this reading…' : flagged(s) ? layer === 'filtered' ? 'Flagged · excluded from temperature field.' : 'Terra flagged this difference from expected.' : !s.assessment.available ? 'Comparison unavailable.' : s.assessment.level === 'moderate' ? `Unusual difference · awaiting ${s.assessment.required_readings} consecutive readings` : 'Within the current comparison.'}</div></>}{view === 'weather' ? <WeatherControls station={s} busy={busy} act={act} onClose={close} /> : view === 'temp' ? <HistoryChart title="Expected and recorded temperature" rows={rows} lines={[['value_f', 'Recorded', '#2f3b2b'], ['expected_f', 'Expected', '#3b528b']]} unit="°" onClose={close} /> : view === 'chill' ? <HistoryChart title={season ? 'Chill hours (32–45°F) since Nov 1' : 'Chill hours (32–45°F) this replay'} rows={chillRows} lines={[['chill_hours', 'Chill hours', '#21918c']]} unit=" h" onClose={close} /> : <><button className="weather-open" onClick={() => setView('weather')}><span>Humidity, wind & solar</span><CaretRight size={12} /></button><div className="station-list-heading">Station reporting</div><div className="station-list">{state.stations.map(row => <div className={`station-row${row.id === selected ? ' active' : ''}`} key={row.id}><button className="station-select" onClick={() => {
          setSelected(row.id);
        }}><span className={`row-dot${!row.online ? ' off' : flagged(row) ? ' warn' : ''}`} /><span>{row.name}</span><span>{row.online ? degrees(row.value_f) : '—'}</span></button><button className="reporting-toggle" aria-label={`${row.online ? 'Take' : 'Bring'} ${row.name} ${row.online ? 'offline' : 'online'}`} aria-pressed={row.online} disabled={busy} onClick={() => {
          clearTimeout(timer.current);
          act({
            type: 'online',
            station_id: row.id,
            online: !row.online
          });
        }}><span /></button></div>)}</div><div className="support-summary"><span>Orchard support</span><strong>{stationSupport(state)} of {state.stations.length}</strong></div><p className="control-footnote">Temperature changes apply to this hour only.</p></>}</aside>;
}
// Controls for a map spot or test sensor. Setting a spot's temperature places a test sensor there.
function SpotControl({
  state,
  target,
  sensor,
  reading,
  layer,
  act,
  busy,
  onPlaced,
  onRemoved,
  onMovedOrchard
}) {
  const check = sensor ? target.check : null,
    flaggedSensor = Boolean(check && check.level !== 'none'),
    estimate = reading?.layers?.[layer],
    start = sensor ? target.value_f : estimate?.status === 'ok' ? Math.round(estimate.temperature_f * 10) / 10 : 50,
    [draft, setDraft] = useState(String(start)),
    timer = useRef(null);
  useEffect(() => {
    setDraft(String(start));
  }, [target.id, start]);
  useEffect(() => () => clearTimeout(timer.current), []);
  function change(v) {
    const n = Number(v);
    setDraft(v);
    clearTimeout(timer.current);
    if (!String(v).trim() || !Number.isFinite(n) || n < -10 || n > 125) return;
    timer.current = setTimeout(async () => {
      if (sensor) {
        act({
          type: 'sensor_value',
          sensor_id: target.id,
          value_f: n
        });
        return;
      }
      const next = await act({
        type: 'sensor',
        lat: target.lat,
        lon: target.lon,
        value_f: n
      });
      const placed = next?.sensors?.at(-1);
      if (placed) onPlaced(target.id, placed.id);
    }, 350);
  }
  return <aside className="station-controls" aria-label={sensor ? 'Selected test sensor controls' : 'Selected map spot controls'}><div className="selected-station-heading"><h2>{sensor ? 'Test sensor' : 'Map spot'}</h2><span className={`station-state${flaggedSensor ? ' is-flagged' : ''}`}>{flaggedSensor ? 'Flagged' : sensor ? 'Uncalibrated' : 'Estimate'}</span></div><p className="spot-coordinates">{target.lat.toFixed(3)}, {target.lon.toFixed(3)} · 60 ft</p><div className="temperature-control"><label htmlFor="spot-temperature">{sensor ? 'Sensor reading' : 'Set a test reading'} <span>°F</span></label><div className="temperature-value"><input id="spot-temperature" aria-label={`${sensor ? 'Test sensor' : 'Map spot'} temperature Fahrenheit`} type="number" min="-10" max="125" step="0.1" value={draft} disabled={busy && !sensor} onChange={e => change(e.target.value)} /><span>°</span></div><input className="temperature-slider" aria-label={`Adjust ${sensor ? 'test sensor' : 'map spot'} temperature`} type="range" min="-10" max="125" step="0.1" value={draft} onChange={e => change(e.target.value)} /></div>{sensor ? <div className="station-comparison"><div><span>Expected</span><strong>{check?.expected_f == null ? '—' : degrees(check.expected_f)}</strong></div><div><span>Difference</span><strong>{check?.deviation_f == null ? '—' : signed(check.deviation_f)}</strong></div></div> : <div className="station-comparison"><div><span>Estimated here</span><strong>{spotValue(reading, layer)}</strong></div><div><span>Layer</span><strong>{layer === 'raw' ? 'Raw' : 'Filtered'}</strong></div></div>}<p className="reading-state" aria-live="polite">{!sensor ? 'Changing the temperature places a test sensor here.' : flaggedSensor ? `Flagged · ${check.level === 'strong' ? `more than ${check.extreme_f.toFixed(1)}° from` : `${check.sustained_readings} hours outside`} what nearby stations predict. The grower gets a text.` : check?.expected_f == null ? 'No comparison: not enough nearby station readings.' : 'Within the range nearby stations predict.'}</p>{sensor && <section className="sensor-weather" aria-label="Test sensor humidity, wind and solar"><div className="station-list-heading">Humidity, wind & solar <span className="muted">· sensor {target.id}</span></div><div style={{display: 'grid', gap: 10}}>{weatherVariables.map(([field, label, unit, max]) => <SensorWeatherControl key={`${target.id}:${field}`} sensor={target} field={field} label={label} unit={unit} max={max} busy={busy} act={act} />)}</div></section>}<div className="spot-actions"><button className="quiet-button" disabled={busy} onClick={() => act({
      type: 'orchard',
      lat: target.lat,
      lon: target.lon
    }).then(next => next && onMovedOrchard())}><MapPin size={13} />Set as orchard</button><button className="quiet-button" disabled={busy} onClick={() => sensor ? act({
      type: 'sensor_remove',
      sensor_id: target.id
    }).then(onRemoved) : onRemoved()}><X size={13} />{sensor ? 'Remove sensor' : 'Remove spot'}</button></div></aside>;
}
function ReturnToLiveChat() {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pending = useRef(false);
  if (sourceIdentity().kind !== 'simulation') return null;
  async function returnToLive() {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const signal = AbortSignal.timeout(60000);
      const status = await fetch('/api/sms/status', { signal, cache: 'no-store' });
      const guard = status.ok ? await status.json() : null;
      if (!guard?.control_token) throw new Error('Reload Terra to return to live chat.');
      const response = await fetch('/api/simulation/connection', { method: 'DELETE', signal, headers: { 'X-Terra-SMS-Control': guard.control_token } });
      if (!response.ok || (await response.json()).connected !== false) throw new Error('Finish the current request, then return to live chat.');
      try {
        const liveFarm = localStorage.getItem('terra.simulation.liveFarm');
        if (liveFarm) localStorage.setItem('terra.tara.activeFarm', liveFarm);
        else localStorage.removeItem('terra.tara.activeFarm');
        localStorage.removeItem('terra.simulation.liveFarm');
      } catch { /* The live server retains the original farm selection. */ }
      window.location.assign('/');
    } catch (reason) {
      setError(reason.name === 'AbortError' ? 'Returning took too long. Try again.' : reason.message);
    } finally { pending.current = false; setBusy(false); }
  }
  return <><button className="icon-button" type="button" onClick={returnToLive} disabled={busy} aria-label="Return to live chat" title="Return to live chat">{busy ? <CircleNotch className="spin" size={16} /> : <ArrowLeft size={16} />}</button>{error && <span className="reading-state" role="alert">{error}</span>}</>;
}

function StationApp({
  demo
}) {
  const {
      state,
      act,
      busy
    } = demo,
    // Nothing is selected on the map until clicked; the panel defaults to the first station.
    [selected, setSelected] = useState(null),
    [layer, setLayer] = useState('raw'),
    [spots, setSpots] = useState([]),
    readings = useSpotReadings(spots, state.heatmap.fingerprint),
    phone = usePhone(state, 'stations'),
    available = supported(state.assessment),
    health = state.monitor.demo_health,
    selectedSpot = spots.find(s => s.id === selected),
    selectedSensor = (state.sensors || []).find(s => s.id === selected),
    stationId = state.stations.some(s => s.id === selected) ? selected : state.stations[0].id,
    edited = state.stations.some(s => s.override !== null && s.override !== undefined) || (state.sensors || []).length > 0;
  function addSpot(lat, lon) {
    const id = `spot:${lat.toFixed(4)},${lon.toFixed(4)}`;
    setSpots(current => [...current.filter(s => s.id !== id), {
      id,
      lat,
      lon
    }].slice(-MAX_SPOTS));
    setSelected(id);
  }
  function removeSpot(id) {
    setSpots(current => current.filter(s => s.id !== id));
  }
  return <><PageNav view="stations" state={state} /><main className="lab-window"><header className="lab-header"><div className="station-app-brand"><img src="/terra-cube-1080.png" alt="terra" width="30" height="30" /><h1>Station lab</h1></div><div className="lab-header-actions"><ReturnToLiveChat /><a className="quiet-button" href="/terra" target="_blank" rel="noreferrer">Open Terra <ArrowUpRight size={14} /></a></div></header><div className="lab-body"><div className="map-column"><WeatherMap state={state} selected={selected} onSelect={setSelected} layer={layer} spots={spots} readings={readings} onAddSpot={addSpot} /><div className="map-bottom"><div className="segmented compact" aria-label="Heatmap layer"><button aria-pressed={layer === 'raw'} onClick={() => setLayer('raw')} title="Every station's reading">Raw</button><button aria-pressed={layer === 'filtered'} onClick={() => setLayer('filtered')} title="Stations Terra flagged are left out">Terra filtered</button></div><span className="evaluation-state" role="status">{busy && <><CircleNotch className="spin" size={12} />Evaluating…</>}</span></div></div>{selectedSpot || selectedSensor ? <SpotControl key={selected} state={state} target={selectedSpot || selectedSensor} sensor={Boolean(selectedSensor)} reading={selectedSpot && readings[selectedSpot.id]} layer={layer} act={act} busy={busy} onPlaced={(spotId, sensorId) => {
          removeSpot(spotId);
          setSelected(sensorId);
        }} onRemoved={() => {
          if (selectedSpot) removeSpot(selectedSpot.id);
          setSelected(null);
        }} onMovedOrchard={() => {
          if (selectedSpot) removeSpot(selectedSpot.id);
          setSelected(null);
        }} /> : <StationControl state={state} selected={stationId} setSelected={setSelected} act={act} busy={busy} layer={layer} />}</div><footer className="lab-footer"><div className="scenario-controls"><button disabled={busy || !edited} onClick={() => act({
            type: 'reset_temperatures'
          })}><ArrowClockwise size={12} />Reset temperatures</button></div><div className="time-controls"><span>{clock(state.clock.current_utc, true)} · {clock(state.clock.current_utc)} PST</span><button className="quiet-button" disabled={busy || state.controls.remaining_hours < 1} onClick={() => act({
            type: 'advance',
            hours: 1
          })}>+1 hour <ArrowRight size={13} /></button><button className="icon-button" disabled={busy} title="Reset entire demo" aria-label="Reset entire demo" onClick={() => act({
            type: 'reset'
          })}><ArrowClockwise size={15} /></button></div></footer></main>{phone.notice && <Notification notification={phone.notice} onClose={() => phone.setNotice(null)} />}</>;
}
function useNoteDetails(id, runId) {
  const [details, setDetails] = useState(null),
    [error, setError] = useState('');
  useEffect(() => {
    setDetails(null);
    setError('');
    if (!id) return;
    const controller = new AbortController();
    fetch(`/api/demo/messages/${encodeURIComponent(id)}/details`, {
      signal: controller.signal
    }).then(async r => {
      if (!r.ok) throw new Error('The note’s evidence could not be read.');
      return r.json();
    }).then(setDetails).catch(e => {
      if (e.name !== 'AbortError') setError(e.message);
    });
    return () => controller.abort();
  }, [id, runId]);
  return {
    details,
    error
  };
}
function Evidence({
  state,
  selected,
  setSelected,
  message,
  details,
  onClose,
  narrow
}) {
  const [tab, setTab] = useState('temperature'),
    panelRef = useRef(null);
  useEffect(() => {
    if (narrow) panelRef.current?.querySelector('.close-evidence')?.focus();
  }, [narrow]);
  function trapFocus(event) {
    if (!narrow || event.key !== 'Tab') return;
    const items = [...panelRef.current.querySelectorAll('button:not([disabled]),select,summary,a[href]')].filter(el => el.offsetParent !== null),
      first = items[0],
      last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
  const snapshot = details?.demo_snapshot,
    historical = !!message,
    pending = historical && !details;
  const packet = snapshot?.assessment || details?.assessments?.[0] || (!historical ? state.assessment : null);
  const stations = snapshot?.stations || (!historical ? state.stations : []);
  const station = stations.find(row => row.id === selected);
  const finding = packet?.station_findings?.find(f => f.station_id === selected);
  const ev = finding?.evidence?.find(row => Date.parse(row.timestamp) === Date.parse(packet.query.valid_utc));
  const observed = station?.value_f ?? ev?.temperature_f,
    expected = station?.assessment?.expected_f ?? ev?.expected_f,
    residual = station?.assessment?.residual_f ?? ev?.residual_f;
  const at = snapshot?.timestamp || packet?.query?.valid_utc || state.clock.current_utc;
  useEffect(() => {
    if (message?.event_types?.some(type => type.startsWith('monitoring_'))) setTab('support');
  }, [message?.id]);
  return <aside className="evidence-panel" ref={panelRef} role={narrow ? 'dialog' : undefined} aria-modal={narrow ? true : undefined} aria-label="Weather evidence" onKeyDown={trapFocus}><header className="evidence-header"><div className="evidence-tabs" role="tablist" aria-label="Evidence" onKeyDown={event => {
        if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const value = event.key === 'Home' ? 'temperature' : event.key === 'End' ? 'support' : tab === 'temperature' ? 'support' : 'temperature';
          setTab(value);
          event.currentTarget.querySelectorAll('button')[value === 'temperature' ? 0 : 1].focus();
        }
      }}><button role="tab" aria-selected={tab === 'temperature'} onClick={() => setTab('temperature')}>Temperature</button><button role="tab" aria-selected={tab === 'support'} onClick={() => setTab('support')}>Support</button></div><button className="icon-button close-evidence" aria-label="Close evidence" onClick={() => onClose()}><X size={15} /></button></header><div className="evidence-body">
 {message && <button className="live-evidence-button" onClick={() => onClose('latest')}>Show latest reading <ArrowRight size={11} /></button>}
 {pending ? <p className="small-note" role="status">Opening the note’s evidence…</p> : tab === 'temperature' ? <>
 <label className="evidence-station-select"><select aria-label="Station evidence" value={selected} onChange={e => setSelected(Number(e.target.value))}>{state.stations.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select><CaretDown size={12} /></label>
 <span className="evidence-time">{clock(at)} PST · {historical ? 'Note evidence' : 'Latest replay reading'}</span>
 <div className="evidence-numbers"><div><span>Observed</span><strong>{degrees(observed)}</strong></div><div><span>Expected</span><strong>{degrees(expected)}</strong></div></div>
 <div className="departure-row"><span>Difference from expected</span><strong>{signed(residual)}</strong></div>
 {(!historical || snapshot) && <Comparison state={state} stationId={selected} history={snapshot?.history} cutoff={historical ? at : null} />}
 <details className="disclosure"><summary><Info size={15} /><span>Details</span><CaretRight size={12} /></summary><div><p>{finding ? 'This station departed from its calibrated comparison. This is a finding, not confirmed sensor failure.' : station?.online === false ? 'This station’s reading was withheld.' : !station?.assessment?.available ? 'Comparison unavailable with the retained inputs.' : 'No station finding is present in this assessment.'}</p><dl><div><dt>Recorded</dt><dd>{degrees(station?.original_f)}F</dd></div><div><dt>Assessment</dt><dd>{(finding?.reason || station?.assessment?.reason || 'Unavailable').replaceAll('_', ' ')}</dd></div></dl></div></details>
 </> : <><h2>Orchard weather support</h2><span className="evidence-time">{clock(at)} PST · {historical ? 'Note evidence' : 'Latest replay reading'}</span><div className="support-count"><strong>{support(packet)}<span> / {state.stations.length}</span></strong><span>usable stations</span></div><p className="support-explanation">{supported(packet) ? 'Independent station readings support the orchard estimate.' : 'The available readings do not support a reliable orchard estimate.'}</p><div className="evidence-stations">{state.stations.map(row => {
            const excluded = packet?.location_estimate.excluded?.find(e => e.station_id === row.id),
              used = packet?.location_estimate.used_station_ids?.includes(row.id);
            return <button key={row.id} onClick={() => {
              setSelected(row.id);
              setTab('temperature');
            }}><span>{row.name}</span><span className={`station-state${used ? '' : ' unavailable'}`}>{used ? 'Used' : excluded ? 'Excluded' : 'Unavailable'}</span><CaretRight size={12} /></button>;
          })}</div><p className="small-note">Station flags and unavailable comparisons remain separate. Recovery requires three consecutive usable hourly estimates.</p></>}
 <details className="disclosure"><summary><MapPin size={15} /><span>Orchard</span><CaretRight size={12} /></summary><div><dl><div><dt>Location</dt><dd>Demonstration orchard</dd></div><div><dt>Estimate</dt><dd>{supported(packet) ? `${number(packet.location_estimate.temperature)}°F` : 'Unavailable'}</dd></div><div><dt>Method</dt><dd>Nathan’s location estimator</dd></div></dl></div></details>
 <details className="disclosure"><summary><Info size={15} /><span>Sources</span><CaretRight size={12} /></summary><div><p>Real CIMIS weather · March 2026. Separate injected copy. Training through August 2025; calibration through November 2025.</p><p>Frozen statistical QC. No model refit. The simulated phone uses the same grower note; no SMS is sent.</p><button className="text-button" onClick={() => {
            const blob = new Blob([JSON.stringify({
                source: state.source,
                assessment: packet,
                demo_snapshot: snapshot
              }, null, 2)], {
                type: 'application/json'
              }),
              link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'terra-demo-evidence.json';
            link.click();
            setTimeout(() => URL.revokeObjectURL(link.href), 0);
          }}>Download evidence <ArrowUpRight size={12} /></button></div></details>
 </div></aside>;
}
function TerraApp({
  demo
}) {
  const {
      state,
      act,
      busy
    } = demo,
    [selected, setSelected] = useState(6),
    [narrow, setNarrow] = useState(() => innerWidth < 740),
    [panel, setPanel] = useState(() => innerWidth >= 740),
    [selectedNote, setSelectedNote] = useState(() => new URLSearchParams(location.search).get('note') || null),
    [question, setQuestion] = useState(''),
    [asking, setAsking] = useState(false),
    [phoneOpen, setPhoneOpen] = useState(false),
    [sent, setSent] = useState({}),
    phone = usePhone(state, 'terra'),
    scroll = useRef(null),
    scrollRun = useRef(null),
    nearBottom = useRef(true),
    opener = useRef(null),
    run = useRef(state.run_id),
    note = state.messages.find(m => m.id === selectedNote),
    {
      details,
      error
    } = useNoteDetails(note?.id, state.run_id);
  useEffect(() => {
    const media = matchMedia('(max-width:739px)'),
      update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (run.current !== state.run_id) {
      run.current = state.run_id;
      setSelectedNote(null);
      setQuestion('');
      setSent({});
    }
  }, [state.run_id]);
  useEffect(() => {
    const initial = scrollRun.current !== state.run_id;
    if (scroll.current && (initial || nearBottom.current)) scroll.current.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: initial || matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'
    });
    scrollRun.current = state.run_id;
  }, [state.messages.length, state.run_id]);
  useEffect(() => {
    if (!panel && opener.current) {
      const frame = requestAnimationFrame(() => opener.current?.focus());
      return () => cancelAnimationFrame(frame);
    }
  }, [panel]);
  useEffect(() => {
    const key = e => {
      if (e.key === 'Escape') {
        if (phoneOpen) setPhoneOpen(false);else if (panel && innerWidth < 740) {
          setPanel(false);
          opener.current?.focus();
        }
      }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [panel, phoneOpen]);
  async function ask(e) {
    e.preventDefault();
    const text = question.trim();
    if (!text || asking) return;
    setAsking(true);
    const result = await act({
      text,
      request_id: crypto.randomUUID()
    }, '/api/demo/questions');
    if (result) {
      setSent(old => ({
        ...old,
        [result.message.id]: text
      }));
      setQuestion('');
    }
    setAsking(false);
  }
  function openNote(m, e) {
    opener.current = e.currentTarget;
    setSelectedNote(m.id);
    setPanel(true);
  }
  function closeEvidence(mode) {
    if (mode === 'latest') setSelectedNote(null);else {
      setPanel(false);
      opener.current?.focus();
    }
  }
  const latest = (state.notifications || []).at(-1),
    available = supported(state.assessment);
  return <><PageNav view="terra" state={state} /><main className={`terra-window${panel ? ' with-evidence' : ''}`}><section className="conversation" inert={panel && narrow ? true : undefined}><header className="thread-header"><div className="terra-identity"><strong>terra</strong><span className={`presence${busy ? ' thinking' : ''}`}>{busy ? 'Checking' : 'Watching'}</span></div><div className="thread-actions"><button className="icon-button" aria-label="Show simulated phone" aria-expanded={phoneOpen} onClick={() => setPhoneOpen(v => !v)}><BellSimple size={16} />{latest && <span className="unread-dot" />}</button><button className="icon-button" aria-label={panel ? 'Hide evidence' : 'Show evidence'} aria-expanded={panel} onClick={e => {
              opener.current = e.currentTarget;
              setPanel(v => !v);
            }}><SidebarSimple size={17} /></button></div></header><div className="thread-scroll" ref={scroll} onScroll={event => {
          const el = event.currentTarget;
          nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}><div className="thread-date">{clock(state.clock.current_utc, true)}</div><div className="thread-context">Demo orchard · historical replay</div>{!available && <div className="quiet-availability"><span className="status-dot limited" />Weather support limited</div>}{state.messages.some(m => !m.event_types?.includes('welcome')) ? <div className="grower-notes" role="log" aria-label="Grower alerts" aria-live="polite" aria-relevant="additions text">{state.messages.filter(m => !m.event_types?.includes('welcome')).map(m => <article key={m.id} className="grower-note"><time dateTime={m.created_at}>{clock(m.created_at)} PST</time>{(sent[m.id] || m.llm_input?.question?.text) && <div className="outgoing-bubble">{sent[m.id] || m.llm_input?.question?.text}</div>}<button className={`incoming-bubble${selectedNote === m.id ? ' selected-note' : ''}`} onClick={e => openNote(m, e)} aria-label={`Open grower note: ${m.text}`}>{m.text}</button><button className="note-attachment" onClick={e => openNote(m, e)}><MapTrifold size={20} /><span><strong>{m.event_types?.includes('monitoring_recovered') ? 'Weather support restored' : m.event_types?.includes('monitoring_limited') ? 'Weather support' : 'Field note'}</strong><small>{m.kind === 'answer' ? 'Supporting facts' : 'View station evidence'}</small></span><ArrowUpRight size={13} /></button><span className="note-delivery">{m.kind === 'answer' ? 'Reply in Terra' : m.event_types?.includes('welcome') ? 'Monitoring started' : 'Demo phone · simulated'}</span></article>)}</div> : <div className="quiet-state"><span className="quiet-orbit"><Check size={19} /></span><h2>All quiet.</h2><p>Weather support is usable.</p><a href="/stations" target="_blank" rel="noreferrer">Open station lab <ArrowUpRight size={12} /></a></div>}</div><div className="chat-bottom"><form className="composer" onSubmit={ask}><span className="composer-icon"><Plus size={14} /></span><input aria-label="Ask Terra about this orchard" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about your orchard…" disabled={asking} maxLength={500} /><button type="submit" disabled={!question.trim() || asking} aria-label="Send question">{asking ? <CircleNotch size={15} className="spin" /> : <ArrowUp size={16} />}</button></form></div>{phoneOpen && <div className="phone-popover"><div className="popover-title"><strong>Simulated phone</strong><button className="icon-button" onClick={() => setPhoneOpen(false)} aria-label="Close simulated phone"><X size={14} /></button></div>{latest ? <><span className="phone-meta">terra · {clock(latest.created_at)} PST</span><p>{latest.text}</p><button className="text-button" onClick={() => {
              setSelectedNote(latest.id);
              setPanel(true);
              setPhoneOpen(false);
            }}>Open this note <ArrowRight size={13} /></button></> : <p>No notifications yet. A meaningful orchard change will appear here.</p>}<small>Local simulation · no provider delivery</small></div>}</section>{panel && <Evidence state={state} selected={selected} setSelected={setSelected} message={note} details={details} onClose={closeEvidence} narrow={narrow} />}</main>{error && <p className="evidence-error" role="alert">{error}</p>}<div className="below-app terra-below"><span>{clock(state.clock.current_utc)} PST · replay time</span><a href="/stations" target="_blank" rel="noreferrer">Station lab <ArrowUpRight size={12} /></a></div>{phone.notice && !phoneOpen && <Notification notification={phone.notice} onClose={() => phone.setNotice(null)} />}</>;
}
export function App() {
  const demo = useDemo(),
    view = location.pathname.startsWith('/terra') ? 'terra' : 'stations';
  useEffect(() => {
    document.title = view === 'terra' ? 'Terra · grower chat' : 'Station lab · Terra simulation';
    const unlock = () => unlockAudio();
    document.addEventListener('pointerdown', unlock, {
      once: true
    });
    return () => document.removeEventListener('pointerdown', unlock);
  }, [view]);
  return <div className={`app-root ${view}-view`}>{demo.state ? view === 'terra' ? <TerraApp demo={demo} /> : <StationApp demo={demo} /> : <div className="initial-load" role="status"><CircleNotch size={20} className="spin" /><span>Opening the saved weather…</span></div>}{demo.error && <div className="connection-error" role="alert"><WarningCircle size={15} /><span>{demo.error}</span><button onClick={() => location.reload()}>Reconnect</button></div>}</div>;
}
