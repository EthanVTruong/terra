import React, { useId } from "react";
import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { clockTime as time, temperature as temp } from "./presentation";

export default function EvidenceChart({ finding }) {
  const description = useId();
  const data = (Array.isArray(finding?.evidence) ? finding.evidence : []).filter((row) => row && Number.isFinite(Date.parse(row.timestamp))).map((row) => ({
    timestamp: Date.parse(row.timestamp), observed: Number.isFinite(row.temperature_f) ? row.temperature_f : null, expected: Number.isFinite(row.expected_f) ? row.expected_f : null,
    range: [row.typical_low_f, row.typical_high_f].every(Number.isFinite) && row.typical_low_f <= row.typical_high_f ? [row.typical_low_f, row.typical_high_f] : null,
  })).sort((a, b) => a.timestamp - b.timestamp);
  if (!data.length) return <p className="chart-empty">Hourly evidence isn’t included in this packet.</p>;
  return <figure className="evidence-chart" aria-describedby={description}>
    <ResponsiveContainer width="100%" height={194} minWidth={0}>
      <ComposedChart data={data} margin={{ top: 14, right: 6, bottom: 0, left: -24 }}>
        <XAxis dataKey="timestamp" type="number" domain={["dataMin", "dataMax"]} ticks={data.map((row) => row.timestamp)} tickFormatter={time} tickLine={false} axisLine={false} minTickGap={38} tick={{ fill: "#777", fontSize: 10 }} dy={7} />
        <YAxis domain={[(min) => Math.floor(min - 3), (max) => Math.ceil(max + 3)]} tickLine={false} axisLine={false} tickCount={4} tick={{ fill: "#777", fontSize: 10 }} tickFormatter={(value) => `${value}°`} />
        <Tooltip cursor={{ stroke: "#d4d4d4", strokeDasharray: "2 3" }} content={({ active, payload, label }) => active && payload?.length ? <div className="chart-tooltip"><time>{time(label)}</time>{payload.filter((row) => row.dataKey !== "range").map((row) => <div key={row.dataKey}><span>{row.name}</span><strong>{temp(row.value)}</strong></div>)}</div> : null} />
        <Area dataKey="range" fill="#e8ece6" fillOpacity={0.8} stroke="none" isAnimationActive={false} tooltipType="none" />
        <Line dataKey="expected" name="Expected" stroke="#82947e" strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
        <Line dataKey="observed" name="Observed" stroke="#383838" strokeWidth={1.6} dot={{ r: 2.5, strokeWidth: 0, fill: "#383838" }} activeDot={{ r: 4, stroke: "#fcfcfc", strokeWidth: 2 }} isAnimationActive={false} connectNulls={false} />
      </ComposedChart>
    </ResponsiveContainer>
    <figcaption id={description} className="chart-legend"><span><i className="observed-key" />Observed</span><span><i className="expected-key" />Expected</span><span><i className="range-key" />Typical range</span><span className="sr-only">{data.slice(-48).map((row) => `${time(row.timestamp)}: observed ${temp(row.observed)}, expected ${temp(row.expected)}.`).join(" ")}</span></figcaption>
  </figure>;
}
