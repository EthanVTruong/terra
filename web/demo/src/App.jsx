import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowRight, ArrowUp, ArrowUpRight, BellSimple, CaretDown, CaretRight, Check, CircleNotch, Info, MapPin, MapTrifold, Plus, SidebarSimple, SpeakerHigh, SpeakerSlash, WarningCircle, X } from '@phosphor-icons/react';
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
const palette = [[35, [89, 148, 190]], [50, [128, 179, 173]], [65, [218, 204, 137]], [80, [225, 155, 105]], [100, [194, 90, 71]], [125, [133, 52, 50]]];
function heatColor(v) {
  for (let i = 1; i < palette.length; i++) {
    if (v <= palette[i][0]) {
      const [a, ca] = palette[i - 1],
        [b, cb] = palette[i],
        t = Math.max(0, Math.min(1, (v - a) / (b - a)));
      return ca.map((n, j) => Math.round(n + (cb[j] - n) * t));
    }
  }
  return palette.at(-1)[1];
}
function HeatField({
  cells
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
        const color = heatColor(v);
        pixels.data[i] = color[0];
        pixels.data[i + 1] = color[1];
        pixels.data[i + 2] = color[2];
        pixels.data[i + 3] = 174;
      }
    });
    sc.putImageData(pixels, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(small, 0, 0, 600, 600);
  }, [cells]);
  return <canvas ref={ref} className="heat-field" aria-hidden="true" />;
}
function BaseMap({
  bounds: b
}) {
  const z = 10,
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
  return <div className="basemap" aria-hidden="true">{tiles.map(t => <img key={`${t.x}-${t.y}`} alt="" draggable="false" src={`https://tile.openstreetmap.org/${z}/${t.x}/${t.y}.png`} style={{
      left: `${t.left}%`,
      top: `${t.top}%`,
      width: `${t.width}%`,
      height: `${t.height}%`
    }} />)}</div>;
}
function fitMapBounds(bounds, aspect) {
  const cx = (bounds.west + bounds.east) / 720 + .5,
    cy = (mercatorY(bounds.north) + mercatorY(bounds.south)) / 2;
  let dx = (bounds.east - bounds.west) / 360,
    dy = mercatorY(bounds.south) - mercatorY(bounds.north);
  if (dx / dy < aspect) dx = dy * aspect;else dy = dx / aspect;
  const latitude = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI;
  return {
    west: (cx - dx / 2 - .5) * 360,
    east: (cx + dx / 2 - .5) * 360,
    north: latitude(cy - dy / 2),
    south: latitude(cy + dy / 2)
  };
}
function WeatherMap({
  state,
  selected,
  onSelect,
  layer
}) {
  const ref = useRef(null),
    [aspect, setAspect] = useState(1.8),
    domain = state.heatmap.bounds,
    b = fitMapBounds(domain, aspect),
    cells = state.heatmap.layers?.[layer] || state.heatmap.cells || [],
    available = cells.some(c => Number.isFinite(c.temperature_f) && c.status === 'ok');
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const box = entries[0].contentRect;
      if (box.height) setAspect(box.width / box.height);
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
  return <div className="weather-map" ref={ref} role="group" aria-label={`${layer === 'raw' ? 'Raw' : 'Terra filtered'} estimated temperature heatmap`}><BaseMap bounds={b} /><div className="heat-domain" style={fieldStyle}><HeatField cells={cells} /></div><div className="map-region">SACRAMENTO VALLEY</div>{state.stations.map(station => {
      const pos = position(station.lat, station.lon, b),
        right = parseFloat(pos.left) > 70,
        bottom = parseFloat(pos.top) > 82;
      return <button key={station.id} className={`map-station${selected === station.id ? ' selected' : ''}${!station.online ? ' is-offline' : flagged(station) ? ' is-flagged' : ''}${right ? ' caption-left' : ''}${bottom ? ' caption-up' : ''}`} style={pos} onClick={() => onSelect(station.id)} aria-label={`Select ${station.name}, ${station.online ? degrees(station.value_f) + ' Fahrenheit' : 'offline'}`} aria-pressed={selected === station.id}><span className="map-point" /><span className="map-station-caption"><strong>{station.name}</strong><span>{station.online ? degrees(station.value_f) : 'Offline'}{station.online && flagged(station) && <WarningCircle size={12} />}</span>{layer === 'filtered' && state.assessment.estimator_qc_exclusions.some(row => row.station_id === station.id) && <small>Excluded from field</small>}</span></button>;
    })}<div className="orchard-pin" style={position(state.orchard.lat, state.orchard.lon, b)}><MapPin size={22} weight="fill" /><span>Demo orchard</span></div>{!available && <div className="map-no-support"><WarningCircle size={18} /><strong>No supported temperature surface</strong><span>Not enough usable station readings</span></div>}<div className="map-legend"><span>35°</span><div>{Array.from({
          length: 32
        }, (_, i) => <i key={i} style={{
          background: `rgb(${heatColor(35 + i / 31 * 90).join(',')})`
        }} />)}</div><span>125°F</span></div><div className="map-attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors</div></div>;
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
            fontFamily: 'Terra Sans',
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
  const [quality, setQuality] = useState('');
  useEffect(() => { setValue(String(reading?.observed ?? '')); setQuality(''); }, [station.id, reading?.observed]);
  const valid = value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= max;
  return <form onSubmit={event => { event.preventDefault(); if (valid) act({type:'weather',station_id:station.id,variable:field,value:Number(value),source_flag:quality}); }}>
    <label htmlFor={`weather-${field}`}>{label} <span>{unit}</span></label>
    <div style={{display:'flex',gap:6,margin:'6px 0'}}>
      <input id={`weather-${field}`} aria-label={`${station.name} ${label}`} type="number" min="0" max={max} step="0.1" value={value} onChange={e=>setValue(e.target.value)} disabled={busy || !station.online} style={{minWidth:0,width:'45%'}} />
      <select aria-label={`${label} source quality`} value={quality} onChange={e=>setQuality(e.target.value)} disabled={busy || !station.online} style={{minWidth:0,width:'55%'}}><option value="">Usable</option><option value="Y">Source warning</option></select>
    </div>
    <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
      <button className="quiet-button" type="submit" disabled={busy || !station.online || !valid}>Apply</button>
      <button className="quiet-button" type="button" disabled={busy || !station.online} onClick={()=>act({type:'weather',station_id:station.id,variable:field,value:null,source_flag:''})}>Missing</button>
      <button className="quiet-button" type="button" disabled={busy} onClick={()=>act({type:'weather_reset',station_id:station.id,variable:field})}>Recorded</button>
    </div>
    <p className="small-note" role="status">{reading?.classification === 'suspected_discrepancy' ? 'Station discrepancy' : reading?.status === 'assessable' ? 'Within comparison policy' : (reading?.reason || 'Unavailable').replaceAll('_',' ')}{reading?.expected != null ? ` · Peers ${number(reading.expected)} ${unit}` : ''}</p>
  </form>;
}
function WeatherControls({station,busy,act}) {
  return <details className="disclosure"><summary><span>Humidity, wind & solar</span><CaretDown size={12}/></summary><div style={{display:'grid',gap:14,padding:'10px 0'}}>
    <p className="small-note">Controlled readings persist as hours advance. Source quality and missing readings stay separate from discrepancies.</p>
    {weatherVariables.map(([field,label,unit,max])=><VariableControl key={`${station.id}:${field}`} station={station} field={field} label={label} unit={unit} max={max} busy={busy} act={act}/>)}
  </div></details>;
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
    latestStation = useRef(s);
  latestStation.current = s;
  const pending = s.online && String(draft).trim() !== '' && Number(draft) !== s.value_f;
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
  return <aside className="station-controls" aria-label="Selected station controls"><div className="selected-station-heading"><h2>{s.name}</h2>{pending ? <span className="station-state">Checking…</span> : <StationLabel station={s} />}</div><div className="temperature-control"><label htmlFor="temperature-value">Temperature <span>°F</span></label><div className="temperature-value"><input id="temperature-value" aria-label={`${s.name} temperature Fahrenheit`} type="number" min="-10" max="125" step="0.1" value={draft} disabled={!s.online || busy} onBlur={() => { if (!String(draft).trim()) setDraft(String(s.value_f ?? s.original_f)); }} onChange={e => change(e.target.value)} /><span>°</span></div><input className="temperature-slider" aria-label={`Adjust ${s.name} temperature`} type="range" min="-10" max="125" step="0.1" value={draft} disabled={!s.online || busy} onChange={e => change(e.target.value)} /><div className="slider-context"><span>Recorded {degrees(s.original_f)}</span><button disabled={s.override === null || s.override === undefined || busy} onClick={() => {
          clearTimeout(timer.current);
          act({
            type: 'temperature',
            station_id: s.id,
            value_f: s.original_f
          });
        }}><ArrowClockwise size={12} />Restore</button></div></div><div className="station-comparison"><div><span>Expected</span><strong>{pending ? '—' : degrees(s.assessment.expected_f)}</strong></div><div><span>Departure</span><strong>{pending ? '—' : signed(s.assessment.residual_f)}</strong></div></div><div className="reading-state" aria-live="polite">{!s.online ? 'Reading withheld from the simulation.' : pending ? 'Evaluating this reading…' : flagged(s) ? layer === 'filtered' ? 'Flagged · excluded from temperature field.' : 'Terra flagged this departure.' : !s.assessment.available ? 'Comparison unavailable.' : s.assessment.level === 'moderate' ? `Departure · awaiting ${s.assessment.required_readings} consecutive readings` : 'Within the current comparison.'}</div><div className="station-list-heading">Station reporting</div><div className="station-list">{state.stations.map(row => <div className={`station-row${row.id === selected ? ' active' : ''}`} key={row.id}><button className="station-select" onClick={() => {
          setSelected(row.id);
        }}><span className={`row-dot${!row.online ? ' off' : flagged(row) ? ' warn' : ''}`} /><span>{row.name}</span><span>{row.online ? degrees(row.value_f) : '—'}</span></button><button className="reporting-toggle" aria-label={`${row.online ? 'Take' : 'Bring'} ${row.name} ${row.online ? 'offline' : 'online'}`} aria-pressed={row.online} disabled={busy} onClick={() => {
          clearTimeout(timer.current);
          act({
            type: 'online',
            station_id: row.id,
            online: !row.online
          });
        }}><span /></button></div>)}</div><div className="support-summary"><span>Orchard support</span><strong>{support(state.assessment)} of {state.stations.length}</strong></div><p className="control-footnote">Temperature changes apply to this hour only.</p></aside>;
}
function StationApp({
  demo
}) {
  const {
      state,
      act,
      busy
    } = demo,
    [selected, setSelected] = useState(6),
    [layer, setLayer] = useState('raw'),
    phone = usePhone(state, 'stations'),
    available = supported(state.assessment),
    health = state.monitor.demo_health;
  return <><PageNav view="stations" state={state} /><main className="lab-window"><header className="lab-header"><div className="station-app-brand"><img src="/terra-cube-1080.png" alt="terra" width="30" height="30" /><h1>Station lab</h1></div><div className="lab-header-actions"><button className="icon-button" onClick={phone.toggleSound} aria-label={phone.sound ? 'Mute demo ping' : 'Enable demo ping'} title={phone.sound ? 'Demo ping on' : 'Demo ping muted'}>{phone.sound ? <SpeakerHigh size={16} /> : <SpeakerSlash size={16} />}</button><a className="quiet-button" href="/terra" target="_blank" rel="noreferrer">Open Terra <ArrowUpRight size={14} /></a></div></header><div className="lab-toolbar"><div className="segmented" aria-label="Heatmap layer"><button aria-pressed={layer === 'raw'} onClick={() => setLayer('raw')}>Raw readings</button><button aria-pressed={layer === 'filtered'} onClick={() => setLayer('filtered')}>Terra filtered</button></div><span className="evaluation-state" role="status">{busy ? <><CircleNotch className="spin" size={12} />Evaluating…</> : <><span className={`status-dot${available ? '' : ' limited'}`} />{available ? health.incident_active ? `Recovery · ${health.consecutive_good_readings}/3 hours` : 'Orchard support usable' : 'Orchard support limited'}</>}</span></div><div className="lab-body"><div className="map-column"><WeatherMap state={state} selected={selected} onSelect={setSelected} layer={layer} /><div className="map-bottom"><span>{layer === 'raw' ? 'Estimated field before Terra QC' : 'Estimated field after Terra QC'}<span className="muted"> · 60 ft</span></span><span>{clock(state.clock.current_utc)} PST</span></div></div><StationControl state={state} selected={selected} setSelected={setSelected} act={act} busy={busy} layer={layer} /></div><footer className="lab-footer"><div className="scenario-controls"><span>Try</span>{[['normal', 'Original'], ['minor', 'Small change'], ['spike', 'Station spike'], [health.incident_active ? 'recover' : 'support_loss', health.incident_active ? 'Restore +3h' : 'Support loss']].map(([name, label]) => <button key={name} disabled={busy || name === 'recover' && state.controls.remaining_hours < 3} onClick={() => act({
            type: 'preset',
            name
          })}>{label}</button>)}</div><div className="time-controls"><span>{clock(state.clock.current_utc, true)}</span><button className="quiet-button" disabled={busy || state.controls.remaining_hours < 1} onClick={() => act({
            type: 'advance',
            hours: 1
          })}>+1 hour <ArrowRight size={13} /></button><button className="icon-button" disabled={busy} title="Reset entire demo" aria-label="Reset entire demo" onClick={() => act({
            type: 'reset'
          })}><ArrowClockwise size={15} /></button></div></footer></main><div className="below-app"><span>Historical replay · modified readings are isolated</span><span>Phone notifications are simulated</span></div>{phone.notice && <Notification notification={phone.notice} onClose={() => phone.setNotice(null)} />}</>;
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
 <div className="departure-row"><span>Departure</span><strong>{signed(residual)}</strong></div>
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
