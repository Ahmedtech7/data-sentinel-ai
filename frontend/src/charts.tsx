import { useMemo, useRef, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import type { FeedDay, RunResults } from './api'
import { fmt } from './ui'

type Feed = NonNullable<RunResults['feed']>

/** Rows received per day vs. the baseline expectation; flagged days marked. */
export function FeedChart({ feed }: { feed: Feed }) {
  const flaggedDays = useMemo(() => {
    const m = new Map<string, FeedDay[]>()
    feed.flagged.forEach((f) => m.set(f.day, [...(m.get(f.day) ?? []), f]))
    return m
  }, [feed])
  const data = feed.daily.map((d) => ({ ...d, flag: flaggedDays.has(d.day) ? d.rows : null }))
  return (
    <div>
      <div className="legend" style={{ marginBottom: 8 }}>
        <span><i className="swatch-line" style={{ background: 'var(--series-1)' }} /> rows received</span>
        <span><i className="swatch-dash" /> expected (baseline median for that weekday)</span>
        <span><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="var(--critical)" /></svg> day with a flagged segment</span>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="day" tickFormatter={fmt.day} minTickGap={28} tickLine={false} />
          <YAxis tickLine={false} axisLine={false} width={48} />
          <Tooltip
            cursor={{ stroke: 'var(--line-strong)' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const p = payload[0].payload as (typeof data)[number]
              const segs = flaggedDays.get(p.day) ?? []
              return (
                <div className="chart-tip">
                  <div className="t">{fmt.day(p.day)}</div>
                  <div className="r"><span><i className="swatch-line" style={{ background: 'var(--series-1)' }} /> received</span><b className="num">{fmt.int(p.rows)}</b></div>
                  <div className="r"><span><i className="swatch-dash" /> expected</span><span className="num">{fmt.int(p.expected)}</span></div>
                  {segs.length > 0 && <div style={{ marginTop: 6, color: 'var(--crit-ink)' }}>Flagged: {segs.map((s) => s.segment).join(', ')}</div>}
                </div>
              )
            }}
          />
          <Line dataKey="expected" stroke="var(--expected)" strokeWidth={2} strokeDasharray="5 5" dot={false} isAnimationActive={false} />
          <Line dataKey="rows" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 5, stroke: 'var(--surface)', strokeWidth: 2 }} isAnimationActive={false} />
          <Scatter dataKey="flag" fill="var(--critical)" stroke="var(--surface)" strokeWidth={2} shape="circle" isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

const SEQ = ['--seq-0', '--seq-1', '--seq-2', '--seq-3', '--seq-4', '--seq-5']

/** Segment × day grid; colour = anomaly score relative to the decision threshold, outline = flagged. */
export function FeedHeatmap({ feed, onPick }: { feed: Feed; onPick?: (f: FeedDay) => void }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string[] } | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const days = useMemo(() => [...new Set(feed.cells.map((c) => c.day))].sort(), [feed])
  const byKey = useMemo(() => new Map(feed.cells.map((c) => [`${c.segment}|${c.day}`, c])), [feed])
  const flagged = useMemo(() => new Map(feed.flagged.map((f) => [`${f.segment}|${f.day}`, f])), [feed])
  const step = (score: number) => Math.min(5, Math.floor((score / feed.threshold) * 4))
  return (
    <div style={{ position: 'relative' }} ref={box}>
      <div className="table-wrap">
        <div className="heat" style={{ gridTemplateColumns: `140px repeat(${days.length}, minmax(9px, 1fr))`, minWidth: 140 + days.length * 11 }} onMouseLeave={() => setTip(null)}>
          {feed.segments.map((s) => (
            <div key={s} style={{ display: 'contents' }}>
              <div className="lab" title={s}>{s}</div>
              {days.map((d) => {
                const c = byKey.get(`${s}|${d}`)
                const f = flagged.get(`${s}|${d}`)
                return (
                  <div
                    key={d}
                    className={`cell ${f ? 'flag' : ''}`}
                    style={{ background: `var(${SEQ[c ? step(c.score) : 0]})`, cursor: f && onPick ? 'pointer' : undefined }}
                    onClick={() => f && onPick?.(f)}
                    onMouseEnter={(e) => {
                      const r = box.current!.getBoundingClientRect()
                      const b = e.currentTarget.getBoundingClientRect()
                      setTip({ x: b.left - r.left + b.width / 2, y: b.top - r.top, text: [
                        `${s} · ${fmt.day(d)}`, `${fmt.int(c?.rows)} rows · score ${c?.score.toFixed(2)} (threshold ${feed.threshold.toFixed(2)})`,
                        ...(f ? [`Flagged: ${f.reasons[0]?.metric ?? 'combination'}${f.volume_alert ? ' · volume test' : ''}`] : []),
                      ] })
                    }}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      {tip && (
        <div className="chart-tip" style={{ position: 'absolute', left: tip.x, top: tip.y - 8, transform: 'translate(-50%, -100%)', zIndex: 5 }}>
          <div className="t">{tip.text[0]}</div>
          {tip.text.slice(1).map((t) => <div key={t} className="small">{t}</div>)}
        </div>
      )}
      <div className="legend" style={{ marginTop: 10 }}>
        <span>score vs threshold:</span>
        {['low', '', '', 'near', '', 'above'].map((l, i) => <span key={i}><i style={{ width: 14, height: 10, borderRadius: 2, background: `var(${SEQ[i]})`, display: 'inline-block' }} />{l}</span>)}
        <span><i style={{ width: 12, height: 10, borderRadius: 2, boxShadow: '0 0 0 1.5px var(--critical)', display: 'inline-block' }} /> flagged (model or volume test)</span>
      </div>
    </div>
  )
}

/** Distribution of record anomaly scores with the decision threshold. */
export function ScoreHistogram({ hist }: { hist: { edges: number[]; counts: number[]; threshold: number } }) {
  const data = hist.counts.map((c, i) => ({ x: (hist.edges[i] + hist.edges[i + 1]) / 2, c, over: hist.edges[i] >= hist.threshold }))
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 10, right: 12, bottom: 0, left: -8 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="x" type="number" domain={[0, hist.edges[hist.edges.length - 1]]} tickFormatter={(v) => v.toFixed(1)} tickLine={false} />
        <YAxis scale="symlog" tickLine={false} axisLine={false} width={48} allowDataOverflow />
        <Tooltip cursor={{ fill: 'var(--surface-2)' }} content={({ active, payload }) => active && payload?.length ? (
          <div className="chart-tip"><div className="t">score ≈ {(payload[0].payload.x as number).toFixed(2)}</div><div className="num">{fmt.int(payload[0].payload.c)} records</div></div>) : null} />
        <Bar dataKey="c" isAnimationActive={false} radius={[3, 3, 0, 0]} shape={(p: unknown) => {
          const { x, y, width, height, payload } = p as { x: number; y: number; width: number; height: number; payload: { over: boolean } }
          return <rect x={x} y={y} width={Math.max(width, 1)} height={Math.max(height, 0)} rx={2} fill={payload.over ? 'var(--critical)' : 'var(--series-1)'} />
        }} />
        <ReferenceLine x={hist.threshold} stroke="var(--ink)" strokeDasharray="4 3" label={{ value: 'threshold', position: 'insideTopRight', fill: 'var(--ink-2)', fontSize: 11 }} />
      </BarChart>
    </ResponsiveContainer>
  )
}
