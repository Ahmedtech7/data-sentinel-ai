import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, useApi, type DatasetDetail, type DatasetRow, type Run, type RunDetail } from '../api'
import { can, useAuth } from '../App'
import { Empty, ErrorBox, fmt, isBusy, Loading } from '../ui'

interface Fault { type: string; level: string; label: string; description: string; defaults: Record<string, number> }
interface Spec { type: string; column?: string; segment?: string; days?: [string, string]; rate?: number; factor?: number; hours?: number }
interface Scorecard {
  rows: { fault: string; injected: number; detected: number; by_rules: number; by_model: number }[]
  periods: { segment: string; day: string; fault: string; detected: boolean }[]
  schema: { column: string; fault: string; detected: boolean }[]
  other_flagged_rows: number; note: string
}
interface Experiment { id: number; dataset_id: number; spec: Spec[]; seed: number; scorecard: Scorecard | null; run: Run | null; created_at: string }

const NEEDS_NUMERIC = ['unit_error', 'spike', 'sign_flip', 'mean_shift', 'type_corruption']
const NEEDS_COLUMN = [...NEEDS_NUMERIC, 'missing', 'drop_column']

function ScorecardView({ card }: { card: Scorecard }) {
  const pdetected = card.periods.filter((p) => p.detected).length
  return (
    <div className="stack">
      {card.rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Injected into records</th><th className="num">Rows</th><th className="num">Caught</th><th className="num">by rules</th><th className="num">by model</th><th>Recall</th></tr></thead>
          <tbody>{card.rows.map((r) => (
            <tr key={r.fault}><td>{r.fault.replace('_', ' ')}</td><td className="num">{fmt.int(r.injected)}</td><td className="num"><b>{fmt.int(r.detected)}</b></td>
              <td className="num">{fmt.int(r.by_rules)}</td><td className="num">{fmt.int(r.by_model)}</td>
              <td style={{ minWidth: 120 }}><div className="row"><div className="bar grow"><i style={{ width: fmt.pct(r.detected / Math.max(r.injected, 1)) }} /></div><span className="num small">{fmt.pct(r.detected / Math.max(r.injected, 1), 0)}</span></div></td></tr>
          ))}</tbody>
        </table></div>
      )}
      {card.periods.length > 0 && (
        <div className="stack-sm">
          <div className="row"><b>Segment-days affected</b><span className="muted small">{pdetected} of {card.periods.length} flagged by the feed model or volume test</span></div>
          <div className="row wrap" style={{ gap: 6 }}>
            {card.periods.slice(0, 40).map((p) => (
              <span key={p.segment + p.day} className="pill"><span className={`check ${p.detected ? 'yes' : 'no'}`}>{p.detected ? '✓' : '✗'}</span>{p.segment} · {fmt.day(p.day)} · {p.fault.replace('_', ' ')}</span>
            ))}
          </div>
        </div>
      )}
      {card.schema.map((s) => (
        <div key={s.column + s.fault} className="row"><span className={`check ${s.detected ? 'yes' : 'no'}`}>{s.detected ? '✓' : '✗'}</span> {s.fault.replace('_', ' ')} on <code>{s.column}</code> {s.detected ? 'reported as a schema/type finding' : 'not reported'}</div>
      ))}
      <p className="small muted">{fmt.int(card.other_flagged_rows)} other rows were flagged. {card.note}</p>
    </div>
  )
}

export default function Lab() {
  const { me } = useAuth()
  const faults = useApi<Fault[]>('/api/lab/faults')
  const datasets = useApi<DatasetRow[]>('/api/datasets')
  const [dsId, setDsId] = useState<number | null>(null)
  const ds = useApi<DatasetDetail>(dsId ? `/api/datasets/${dsId}` : null)
  const latestRun = ds.data?.versions.find((v) => v.run?.status === 'succeeded')?.run?.id
  const run = useApi<RunDetail>(latestRun ? `/api/runs/${latestRun}` : null)
  const [specs, setSpecs] = useState<Spec[]>([])
  const [seed, setSeed] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const history = useApi<Experiment[]>('/api/lab/experiments', (xs) => xs.some((x) => isBusy(x.run)), 2000)

  useEffect(() => { if (!dsId && datasets.data?.length) setDsId(datasets.data[0].id) }, [datasets.data, dsId])
  const schema = ds.data?.schema
  const numeric = schema?.numeric ?? []
  const segments = run.data?.results?.feed?.segments ?? []
  const days = useMemo(() => run.data?.results?.feed?.daily.map((d) => d.day) ?? [], [run.data])
  const catalog = useMemo(() => new Map(faults.data?.map((f) => [f.type, f])), [faults.data])

  const presets: [string, () => Spec[]][] = [
    ['Pipeline outage', () => [{ type: 'volume_drop', segment: segments[segments.length - 1], days: [days[3], days[4]] as [string, string], rate: 0.9 }]],
    ['Unit conversion bug', () => [{ type: 'unit_error', column: numeric.find((c) => /price|amount|cost/i.test(c)) ?? numeric[0], factor: 100, rate: 0.01 }]],
    ['Replayed load', () => [{ type: 'duplicates', rate: 0.03 }]],
    ['Late feed', () => [{ type: 'delay', segment: segments[0], days: [days[6], days[7]] as [string, string], hours: 8 }]],
    ['Silent drift', () => [{ type: 'mean_shift', column: numeric.find((c) => /price/i.test(c)) ?? numeric[0], segment: segments[segments.length - 1], days: [days[8], days[9]] as [string, string], factor: 1.3 }]],
    ['Schema change', () => [{ type: 'drop_column', column: schema?.columns.find((c) => c.role === 'categorical' && !schema.segments.includes(c.name))?.name ?? schema?.columns.at(-1)?.name }]],
  ]
  const usable = (name: string) => {
    const t = presets.find(([n]) => n === name)![1]()[0]
    return (!t.segment && !t.days || (segments.length && days.length > 9)) && (!NEEDS_COLUMN.includes(t.type) || t.column) && (t.type !== 'delay' || schema?.ingest_time)
  }
  const update = (i: number, patch: Partial<Spec>) => setSpecs(specs.map((s, j) => (j === i ? { ...s, ...patch } : s)))

  const runExperiment = async () => {
    setBusy(true)
    setError(null)
    try {
      await api('/api/lab/experiments', { method: 'POST', json: { dataset_id: dsId, seed, faults: specs } })
      setSpecs([])
      setSeed(seed + 1)
      history.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (datasets.error) return <ErrorBox error={datasets.error} retry={datasets.reload} />
  if (!datasets.data || !faults.data) return <Loading />
  const latest = history.data?.[0]

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head"><div className="grow"><h1>Fault Lab</h1>
        <p className="lede">Break a copy of your latest version on purpose, with a recorded seed, and see exactly what Data Sentinel catches and what it misses. Every experiment becomes a real version and goes through the same analysis as an upload.</p></div></div>
      {!datasets.data.length ? <div className="card"><Empty title="No datasets yet">Upload a dataset first, or open a demo workspace from the home page.</Empty></div> : (
        <div className="split">
          <div className="stack" style={{ minWidth: 0 }}>
            {latest && (
              <div className="card">
                <div className="card-head"><h3 className="grow">Experiment #{latest.id}</h3><span className="small muted">seed {latest.seed} · {fmt.ago(latest.created_at)}</span></div>
                <div className="card-body">
                  {isBusy(latest.run) ? <div className="banner scan"><span className="spinner" /> Analysing the corrupted version: {latest.run?.stage || 'queued'}…</div>
                    : latest.run?.status === 'failed' ? <ErrorBox error={latest.run.error ?? 'Analysis failed'} />
                    : latest.scorecard ? <ScorecardView card={latest.scorecard} /> : null}
                  {latest.run?.status === 'succeeded' && <div style={{ marginTop: 12 }}><Link to={`/app/datasets/${latest.dataset_id}?version=${latest.run.version_id}`}>Open the analysed version →</Link></div>}
                </div>
              </div>
            )}
            <div className="card">
              <div className="card-head"><h3>History</h3></div>
              {history.data?.length ? (
                <div className="table-wrap"><table>
                  <thead><tr><th>#</th><th>Faults</th><th>Result</th><th>When</th></tr></thead>
                  <tbody>{history.data.map((x) => {
                    const inj = x.scorecard?.rows.reduce((a, r) => a + r.injected, 0) ?? 0
                    const det = x.scorecard?.rows.reduce((a, r) => a + r.detected, 0) ?? 0
                    const p = x.scorecard?.periods ?? []
                    return (
                      <tr key={x.id}><td className="num">{x.id}</td><td className="small">{x.spec.map((s) => catalog.get(s.type)?.label ?? s.type).join(', ')}</td>
                        <td className="small">{isBusy(x.run) ? <span className="spinner" /> : x.scorecard ? [inj && `${det}/${inj} rows`, p.length && `${p.filter((q) => q.detected).length}/${p.length} segment-days`, x.scorecard.schema.length && `${x.scorecard.schema.filter((q) => q.detected).length}/${x.scorecard.schema.length} schema`].filter(Boolean).join(' · ') : x.run?.status}</td>
                        <td className="small muted">{fmt.ago(x.created_at)}</td></tr>
                    )
                  })}</tbody>
                </table></div>
              ) : <Empty title="No experiments yet">Pick a scenario on the right to run your first one.</Empty>}
            </div>
          </div>

          <div className="card pad stack sticky" style={{ position: 'sticky', top: 20 }}>
            <label className="field">Dataset
              <select value={dsId ?? ''} onChange={(e) => { setDsId(Number(e.target.value)); setSpecs([]) }}>
                {datasets.data.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select></label>
            {ds.data && !schema ? <div className="notice">This dataset has no baseline yet. Wait for its first analysis.</div> : <>
              <div className="stack-sm">
                <span className="eyebrow">Scenarios</span>
                <div className="row wrap" style={{ gap: 6 }}>
                  {presets.map(([name, make]) => <button key={name} className="btn sm" disabled={!schema || !usable(name)} onClick={() => setSpecs([...specs, ...make()])}>{name}</button>)}
                </div>
              </div>
              {specs.map((s, i) => {
                const f = catalog.get(s.type)!
                return (
                  <div key={i} className="card pad stack-sm" style={{ background: 'var(--surface-2)', padding: 12 }}>
                    <div className="row"><b className="grow">{f.label}</b><button className="btn ghost sm" onClick={() => setSpecs(specs.filter((_, j) => j !== i))} aria-label="Remove fault">✕</button></div>
                    <p className="small muted">{f.description}</p>
                    {NEEDS_COLUMN.includes(s.type) && (
                      <select aria-label="Column" value={s.column ?? ''} onChange={(e) => update(i, { column: e.target.value })}>
                        {(NEEDS_NUMERIC.includes(s.type) ? numeric : schema!.columns.map((c) => c.name)).map((c) => <option key={c}>{c}</option>)}
                      </select>
                    )}
                    {s.segment !== undefined && (
                      <select aria-label="Segment" value={s.segment} onChange={(e) => update(i, { segment: e.target.value })}>{segments.map((g) => <option key={g}>{g}</option>)}</select>
                    )}
                    {s.days && (
                      <div className="row"><select aria-label="From day" value={s.days[0]} onChange={(e) => update(i, { days: [e.target.value, s.days![1] < e.target.value ? e.target.value : s.days![1]] })}>{days.map((d) => <option key={d} value={d}>{fmt.day(d)}</option>)}</select>
                        <span className="muted">→</span><select aria-label="To day" value={s.days[1]} onChange={(e) => update(i, { days: [s.days![0], e.target.value] })}>{days.filter((d) => d >= s.days![0]).map((d) => <option key={d} value={d}>{fmt.day(d)}</option>)}</select></div>
                    )}
                    <div className="row">
                      {s.rate !== undefined && <label className="field grow small">Share of rows<input type="number" min={0.001} max={1} step={0.005} value={s.rate} onChange={(e) => update(i, { rate: Number(e.target.value) })} /></label>}
                      {s.factor !== undefined && <label className="field grow small">Factor<input type="number" min={0.01} max={10000} step={0.1} value={s.factor} onChange={(e) => update(i, { factor: Number(e.target.value) })} /></label>}
                      {s.hours !== undefined && <label className="field grow small">Hours late<input type="number" min={1} max={240} value={s.hours} onChange={(e) => update(i, { hours: Number(e.target.value) })} /></label>}
                    </div>
                  </div>
                )
              })}
              <label className="field">Seed <span className="hint">same seed + same version = same corruption</span><input type="number" min={0} value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></label>
              {error && <div className="error">{error}</div>}
              {can(me, 'analyst') ? (
                <button className="btn primary" disabled={!specs.length || busy || isBusy(latest?.run)} onClick={runExperiment}>{busy ? 'Injecting…' : `Inject ${specs.length || ''} fault${specs.length === 1 ? '' : 's'} & analyse`}</button>
              ) : <p className="small muted">Viewers cannot run experiments.</p>}
              <p className="small muted">Experiments create a new version tagged “fault lab”. They can open incidents like real data would; dismiss them as expected.</p>
            </>}
          </div>
        </div>
      )}
    </div>
  )
}
