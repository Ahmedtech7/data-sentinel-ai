import type { FeedDay, FeedReason, Finding, RecordExample } from './api'
import { fmt } from './ui'

const metricValue = (r: FeedReason, v: number | null) =>
  v == null ? '–' : r.unit === 'share' ? fmt.pct(v) : r.unit === 'hours' ? `${v.toFixed(1)} h` : fmt.num(v)

export function FeedReasons({ day }: { day: FeedDay }) {
  return (
    <div className="stack-sm">
      {day.reasons.map((r, i) => (
        <div key={i} className="row" style={{ alignItems: 'baseline', gap: 12 }}>
          <span style={{ minWidth: 120 }} className="ink2">{r.metric}</span>
          <b className="num">{metricValue(r, r.value)}</b>
          <span className="muted small">vs {metricValue(r, r.typical)} · {r.basis}{r.z != null && ` · ${Math.abs(r.z) >= 50 ? '≥50' : Math.abs(r.z).toFixed(1)}σ`}</span>
        </div>
      ))}
    </div>
  )
}

/** Value against its learned expectation and the baseline peers that support it. */
export function RecordReasons({ ex }: { ex: RecordExample }) {
  return (
    <div className="stack-sm">
      {ex.reasons.map((r) => {
        const lo = Math.min(r.range[0], r.value, r.expected)
        const hi = Math.max(r.range[1], r.value, r.expected)
        const logp = (v: number) => {
          const a = Math.log10(Math.max(lo, 1e-3)), b = Math.log10(Math.max(hi, 1e-3))
          return b === a ? 50 : 4 + ((Math.log10(Math.max(v, 1e-3)) - a) / (b - a)) * 92
        }
        return (
          <div key={r.column} className="stack-sm" style={{ gap: 4 }}>
            <div className="row wrap" style={{ gap: 8, alignItems: 'baseline' }}>
              <code>{r.column}</code>
              <b className="num">{fmt.num(r.value)}</b>
              <span className="muted small">expected ≈ {fmt.num(r.expected)} (usual range {fmt.num(r.range[0])}–{fmt.num(r.range[1])})</span>
              {r.ratio != null && Math.abs(Math.log10(Math.abs(r.ratio) || 1)) > 0.3 && <span className="pill">{r.ratio >= 1 ? `${fmt.num(r.ratio, 1)}× higher` : `${fmt.num(1 / r.ratio, 1)}× lower`}</span>}
            </div>
            <div className="gauge" aria-hidden="true" title="log scale">
              <span style={{ position: 'absolute', left: `${logp(r.range[0])}%`, width: `${Math.max(1, logp(r.range[1]) - logp(r.range[0]))}%`, top: 0, bottom: 0, background: 'var(--seq-2)', borderRadius: 4 }} />
              <i style={{ left: `${logp(r.expected)}%`, background: 'var(--expected)' }} />
              <i style={{ left: `${logp(r.value)}%`, background: 'var(--critical)' }} />
            </div>
            <div className="small muted">
              Baseline evidence: {r.peers.column ? <>{fmt.int(r.peers.rows)} rows with <code>{r.peers.column} = {r.peers.value}</code> had median {fmt.num(r.peers.median)}</> : <>median over {fmt.int(r.peers.rows)} baseline rows was {fmt.num(r.peers.median)}</>}.
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function RecordTable({ examples, limit = 50 }: { examples: RecordExample[]; limit?: number }) {
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Row</th><th>Record</th><th>Why it stands out</th><th className="num">Score</th></tr></thead>
      <tbody>
        {examples.slice(0, limit).map((ex) => (
          <tr key={ex.position}>
            <td className="num small">{ex.row_number}</td>
            <td className="small" style={{ minWidth: 160 }}>
              {ex.key && <div className="mono">{ex.key}</div>}
              <div className="muted">{Object.entries(ex.context).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join(' · ')}</div>
              {ex.rule_violation && <span className="pill" style={{ marginTop: 4 }}>also breaks a rule</span>}
            </td>
            <td style={{ minWidth: 340 }}><RecordReasons ex={ex} /></td>
            <td className="num">{ex.score.toFixed(1)}</td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}

function ExampleRows({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows?.length) return null
  const cols = Object.keys(rows[0]).slice(0, 8)
  return (
    <div className="table-wrap"><table>
      <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className="mono small">{r[c] == null ? <span className="muted">∅</span> : String(r[c])}</td>)}</tr>)}</tbody>
    </table></div>
  )
}

/** Renders the evidence attached to a finding / incident, by kind. */
export function Evidence({ kind, evidence }: { kind: string; evidence: Finding['evidence'] }) {
  const e = evidence as Record<string, never>
  if (kind === 'record_anomaly') return <RecordTable examples={e.examples as RecordExample[]} limit={25} />
  if (kind === 'feed_anomaly')
    return (
      <div className="stack">
        {(e.days as FeedDay[]).map((d) => (
          <div key={d.day} className="stack-sm">
            <div className="row"><b>{fmt.day(d.day)}</b><span className="muted small">{fmt.int(d.rows)} rows{d.expected_rows != null && ` · ${fmt.int(d.expected_rows)} expected`}{d.volume_alert && ' · Poisson volume test fired'} · model score {d.score.toFixed(2)}</span></div>
            <FeedReasons day={d} />
          </div>
        ))}
      </div>
    )
  return (
    <div className="stack">
      {kind === 'schema' && (
        <div className="stack-sm">
          {(e.missing as string[])?.length > 0 && <div>Missing: {(e.missing as string[]).map((c) => <code key={c} style={{ marginRight: 6 }}>{c}</code>)}</div>}
          {(e.added as string[])?.length > 0 && <div>Added: {(e.added as string[]).map((c) => <code key={c} style={{ marginRight: 6 }}>{c}</code>)}</div>}
        </div>
      )}
      {kind === 'drift' && <div className="small ink2">Population stability index {Number(e.psi).toFixed(2)} on <code>{e.column}</code>.</div>}
      {kind === 'missing' && <div className="small ink2"><code>{e.column}</code>: {fmt.pct(e.rate)} empty{e.baseline_rate != null && ` (baseline ${fmt.pct(e.baseline_rate)})`}</div>}
      {kind === 'type' && <div className="small ink2">Unparseable values: {(e.examples as { value: string; count: number }[]).map((x) => <code key={x.value} style={{ marginRight: 6 }}>{x.value} ×{x.count}</code>)}</div>}
      {Array.isArray(e.examples) && kind !== 'type' && <><div className="eyebrow">Example rows</div><ExampleRows rows={e.examples as Record<string, unknown>[]} /></>}
      {Array.isArray(e.segment_days) && (e.segment_days as FeedDay[]).length > 0 && (
        <div className="small ink2">Concentrated in: {(e.segment_days as FeedDay[]).slice(0, 8).map((d) => `${d.segment} on ${fmt.day(d.day)}`).join('; ')}{(e.segment_days as FeedDay[]).length > 8 && '…'}</div>
      )}
    </div>
  )
}
