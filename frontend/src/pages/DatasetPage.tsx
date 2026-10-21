import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, useApi, type DatasetDetail, type FeedDay, type Rule, type RunDetail } from '../api'
import { can, useAuth } from '../App'
import { FeedChart, FeedHeatmap, ScoreHistogram } from '../charts'
import { Evidence, FeedReasons, RecordTable } from '../evidence'
import { Empty, ErrorBox, FileDrop, fmt, isBusy, Loading, Modal, RunStatus, Sev, Stat } from '../ui'

const TABS = ['Findings', 'Feed health', 'Records', 'Contract & rules', 'Versions', 'Models'] as const

function UploadVersion({ id, onDone, onClose }: { id: number; onDone: () => void; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const go = async () => {
    if (!file) return setError('Choose a CSV file first.')
    setBusy(true)
    const body = new FormData()
    body.set('file', file)
    try {
      await api(`/api/datasets/${id}/versions`, { method: 'POST', body })
      onDone()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }
  return (
    <Modal title="Upload a new version" onClose={onClose}>
      <div className="stack">
        <p className="small ink2">The file is checked against this dataset’s contract and scored by its models. Columns should match the baseline; differences are reported as schema findings.</p>
        <FileDrop file={file} onFile={setFile} />
        {error && <div className="error">{error}</div>}
        <div className="row"><span className="grow" /><button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={go} disabled={busy}>{busy ? 'Uploading…' : 'Upload & analyse'}</button></div>
      </div>
    </Modal>
  )
}

function RulesEditor({ ds, onSaved }: { ds: DatasetDetail; onSaved: () => void }) {
  const [rules, setRules] = useState(ds.rules.map((r) => ({ name: r.name ?? '', expr: r.expr ?? '' })))
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const save = async () => {
    try {
      await api(`/api/datasets/${ds.id}/rules`, { method: 'PUT', json: rules.filter((r) => r.expr.trim()) })
      setMsg({ ok: true, text: 'Saved. Rules apply from the next analysis.' })
      onSaved()
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    }
  }
  return (
    <div className="stack-sm">
      {rules.map((r, i) => (
        <div key={i} className="row wrap">
          <input style={{ maxWidth: 220 }} value={r.name} placeholder="Rule name" onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <input className="grow mono" style={{ minWidth: 260 }} value={r.expr} placeholder="abs(revenue - quantity * unit_price) <= 0.01" onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, expr: e.target.value } : x)))} />
          <button className="btn ghost sm" onClick={() => setRules(rules.filter((_, j) => j !== i))} aria-label="Remove rule">✕</button>
        </div>
      ))}
      <div className="row"><button className="btn sm" onClick={() => setRules([...rules, { name: '', expr: '' }])}>Add rule</button><button className="btn primary sm" onClick={save}>Save rules</button></div>
      <p className="small muted">Columns, numbers, + − × ÷, <code>abs()</code> and one comparison. Expressions are parsed into a safe tree, never evaluated as code. Rows with an empty input are skipped (missing values are reported separately).</p>
      {msg && <div className={msg.ok ? 'notice' : 'error'}>{msg.text}</div>}
    </div>
  )
}

const ruleText = (r: Rule) =>
  r.type === 'not_null' ? <><code>{r.column}</code> is never empty</> :
  r.type === 'min' ? <><code>{r.column}</code> ≥ {r.value}</> :
  r.type === 'allowed_values' ? <><code>{r.column}</code> ∈ {'{'}{r.values!.slice(0, 6).join(', ')}{r.values!.length > 6 && `, … ${r.values!.length - 6} more`}{'}'}</> :
  <code>{r.expr}</code>

export default function DatasetPage() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const { me } = useAuth()
  const [tab, setTab] = useState<(typeof TABS)[number]>('Findings')
  const [uploading, setUploading] = useState(false)
  const [picked, setPicked] = useState<FeedDay | null>(null)
  const ds = useApi<DatasetDetail>(`/api/datasets/${id}`, (d) => d.versions.some((v) => isBusy(v.run)))
  const versionId = Number(params.get('version')) || ds.data?.versions[0]?.id
  const version = ds.data?.versions.find((v) => v.id === versionId)
  const runId = version?.run?.status === 'succeeded' ? version.run.id : null
  const run = useApi<RunDetail>(runId ? `/api/runs/${runId}` : null)
  useEffect(() => setPicked(null), [runId])

  if (ds.error) return <ErrorBox error={ds.error} retry={ds.reload} />
  if (!ds.data) return <Loading />
  const d = ds.data
  const res = run.data?.results
  const busyRun = d.versions.find((v) => isBusy(v.run))?.run

  const setBaseline = async (vid: number) => {
    await api(`/api/datasets/${d.id}/baseline`, { method: 'POST', json: { version_id: vid } })
    ds.reload()
  }
  const remove = async () => {
    if (!confirm(`Delete “${d.name}” with all versions, models and incidents? This cannot be undone.`)) return
    await api(`/api/datasets/${d.id}`, { method: 'DELETE' })
    nav('/app/datasets')
  }

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head">
        <div className="grow">
          <div className="crumbs"><Link to="/app/datasets">Datasets</Link> /</div>
          <h1>{d.name}</h1>
          {d.description && <p className="lede">{d.description}</p>}
        </div>
        <div className="row wrap">
          {d.versions.length > 1 && (
            <select aria-label="Version" value={versionId} onChange={(e) => setParams({ version: e.target.value })} style={{ width: 'auto' }}>
              {d.versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.filename}{v.id === d.baseline_version_id ? ' (baseline)' : ''}</option>)}
            </select>
          )}
          {can(me, 'analyst') && <button className="btn primary" onClick={() => setUploading(true)}>Upload new version</button>}
        </div>
      </div>

      {busyRun && (
        <div className="banner scan" role="status"><span className="spinner" /><span>Analysing version {d.versions.find((v) => v.run?.id === busyRun.id)?.number}: <b>{busyRun.stage || busyRun.status}</b>…</span></div>
      )}
      {version?.run?.status === 'failed' && <ErrorBox error={`Analysis of v${version.number} failed: ${version.run.error}`} />}

      {res && (
        <>
          {res.summary.baseline && (
            <div className="notice">
              <b>Baseline version.</b> This version defines “normal” for the dataset. Record scores are out-of-fold (each row scored by models that did not see it) and only the volume test is applied to the feed, so expect few findings here; later uploads are compared against it.
            </div>
          )}
          {res.notes?.map((n) => <div key={n} className="notice">{n}</div>)}
          <div className="grid g4">
            <Stat label="Rows" value={fmt.int(res.rows)} sub={`${res.columns} columns · v${run.data!.version.number} · ${run.data!.version.source.replace('_', ' ')}`} />
            <Stat label="Checks passed" value={`${res.summary.checks_passed}/${res.summary.checks_total}`} sub={`${fmt.int(res.summary.rule_flagged_rows)} rows break a rule`} />
            <Stat label="Unusual records" value={fmt.int(res.summary.model_flagged_rows)} sub={res.records ? `${fmt.pct(res.summary.model_flagged_rows / res.rows, 2)} of rows · ${fmt.detector(res.records.detector)}` : 'record model not trained'} />
            <Stat label="Unusual segment-days" value={fmt.int(res.summary.flagged_periods)} sub={res.feed ? `of ${fmt.int(res.feed.cells.length)} · ${res.feed.segments.length} segments` : 'feed model not trained'} />
          </div>
        </>
      )}
      {runId && run.loading && !res && <Loading rows={1} />}
      {run.error && <ErrorBox error={run.error} retry={run.reload} />}

      <div>
        <div className="tabs" role="tablist">
          {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{t}{t === 'Findings' && res ? ` (${res.findings.length})` : ''}</button>)}
        </div>

        {tab === 'Findings' && (res ? (res.findings.length ? (
          <div className="stack">
            {res.findings.map((f) => (
              <details key={f.fingerprint} className="card" open={f.severity === 'critical' || f.severity === 'high'}>
                <summary className="card-head" style={{ cursor: 'pointer', listStyle: 'none' }}>
                  <Sev s={f.severity} /><h3 className="grow">{f.title}</h3><span className="pill">{f.kind.replace('_', ' ')}</span>
                </summary>
                <div className="card-body stack"><p className="ink2">{f.summary}</p><Evidence kind={f.kind} evidence={f.evidence} /></div>
              </details>
            ))}
            <Link to={`/app/incidents?dataset=${d.id}`} className="small">Track these as incidents →</Link>
          </div>
        ) : <div className="card"><Empty title="Nothing unusual">All checks passed and neither model flagged anything in this version.</Empty></div>)
          : !busyRun && <div className="card"><Empty title="No analysis yet" /></div>)}

        {tab === 'Feed health' && (res?.feed ? (
          <div className="stack">
            <div className="card"><div className="card-head"><h3 className="grow">Rows per day</h3><span className="small muted">{fmt.detector(res.feed.detector)} + Poisson volume test</span></div><div className="card-body"><FeedChart feed={res.feed} /></div></div>
            <div className="card">
              <div className="card-head"><h3 className="grow">Segment × day</h3><span className="small muted">Click an outlined cell for the evidence</span></div>
              <div className="card-body stack"><FeedHeatmap feed={res.feed} onPick={setPicked} />
                {picked && (
                  <div className="card pad stack-sm" style={{ background: 'var(--surface-2)' }}>
                    <div className="row"><b>{picked.segment} · {fmt.day(picked.day)}</b><span className="muted small">{fmt.int(picked.rows)} rows{picked.expected_rows != null && ` · ${fmt.int(picked.expected_rows)} expected`}</span><button className="btn ghost sm right" onClick={() => setPicked(null)}>✕</button></div>
                    <FeedReasons day={picked} />
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : <div className="card"><Empty title="Feed model unavailable">{d.notes.find((n) => n.startsWith('Feed')) ?? 'Feed monitoring needs a timestamp column and at least 14 days of baseline history.'}</Empty></div>)}

        {tab === 'Records' && (res?.records ? (
          <div className="stack">
            <div className="card"><div className="card-head"><h3 className="grow">Anomaly score distribution</h3><span className="small muted">log scale · {fmt.int(res.records.scored)} records · threshold {res.records.threshold.toFixed(2)}</span></div><div className="card-body"><ScoreHistogram hist={res.records.score_histogram} /></div></div>
            <div className="card">
              <div className="card-head"><h3 className="grow">Most unusual records</h3><span className="small muted">top {Math.min(50, res.records.examples.length)} of {fmt.int(res.records.flagged)}</span></div>
              {res.records.examples.length ? <RecordTable examples={res.records.examples} /> : <Empty title="No unusual records" />}
              <div className="card-body small muted">Unusual means far from what the baseline predicts for this context, not necessarily wrong. Promotions, bulk orders and new prices look unusual too; confirm with the source before correcting data.</div>
            </div>
          </div>
        ) : <div className="card"><Empty title="Record model unavailable">{d.notes.find((n) => n.startsWith('Record')) ?? 'The record model needs numeric columns.'}</Empty></div>)}

        {tab === 'Contract & rules' && (
          <div className="grid g2">
            <div className="card"><div className="card-head"><h3>Inferred contract</h3><span className="small muted">from baseline v{d.versions.find((v) => v.id === d.baseline_version_id)?.number}</span></div>
              <div className="card-body stack-sm">{d.contract?.rules.length ? d.contract.rules.map((r, i) => <div key={i} className="small">{ruleText(r)}</div>) : <span className="muted">No contract yet.</span>}
                {d.schema?.key && <div className="small"><code>{d.schema.key}</code> is unique (record key)</div>}</div></div>
            <div className="card"><div className="card-head"><h3>Business rules</h3></div><div className="card-body">
              {can(me, 'analyst') ? <RulesEditor ds={d} onSaved={ds.reload} /> : d.rules.length ? d.rules.map((r, i) => <div key={i} className="small"><b>{r.name}</b>: {ruleText(r)}</div>) : <span className="muted">None.</span>}</div></div>
            {d.schema && (
              <div className="card span2 table-wrap"><table>
                <thead><tr><th>Column</th><th>Role</th><th className="num">Distinct values</th><th>Used for</th></tr></thead>
                <tbody>{d.schema.columns.map((c) => (
                  <tr key={c.name}><td><code>{c.name}</code></td><td>{c.role}</td><td className="num">{fmt.int(c.nunique)}</td>
                    <td className="small muted">{[c.name === d.schema!.key && 'record key', c.name === d.schema!.time && 'event time', c.name === d.schema!.ingest_time && 'ingestion time', d.schema!.segments.includes(c.name) && 'feed segment', d.schema!.context.includes(c.name) && 'model context', d.schema!.numeric.includes(c.name) && 'modelled value'].filter(Boolean).join(' · ') || '—'}</td></tr>
                ))}</tbody></table></div>
            )}
          </div>
        )}

        {tab === 'Versions' && (
          <div className="card table-wrap"><table>
            <thead><tr><th>Version</th><th>File</th><th className="num">Rows</th><th>Analysis</th><th>Uploaded</th><th /></tr></thead>
            <tbody>{d.versions.map((v) => (
              <tr key={v.id}>
                <td><b>v{v.number}</b> {v.id === d.baseline_version_id && <span className="pill accent">baseline</span>}</td>
                <td className="small"><div className="ellipsis" style={{ maxWidth: 260 }}>{v.filename}</div><div className="muted mono" title={v.sha256}>{v.source.replace('_', ' ')} · {fmt.bytes(v.size_bytes)} · sha256 {v.sha256.slice(0, 10)}…</div></td>
                <td className="num">{fmt.int(v.rows)}</td>
                <td><RunStatus run={v.run} /></td>
                <td className="small muted">{fmt.date(v.created_at)}</td>
                <td className="row" style={{ justifyContent: 'flex-end' }}>
                  <button className="btn sm" onClick={() => { setParams({ version: String(v.id) }); setTab('Findings') }}>View</button>
                  {can(me, 'analyst') && v.id !== d.baseline_version_id && <button className="btn sm" title="Re-learn normal from this version and retrain the models" onClick={() => setBaseline(v.id)}>Make baseline</button>}
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        )}

        {tab === 'Models' && (
          <div className="stack">
            <div className="grid g2">
              {d.models.filter((m) => m.active).map((m) => (
                <div key={m.id} className="card pad stack-sm">
                  <span className="eyebrow">{m.kind === 'row' ? 'Record model' : 'Feed model'}</span>
                  <h3 style={{ fontSize: 18 }}>{fmt.detector(m.detector)}</h3>
                  <dl className="reason">
                    <dt>Trained on</dt><dd>v{d.versions.find((v) => v.id === m.trained_on_version_id)?.number} · {fmt.int(m.info.rows)} rows{m.info.days ? ` · ${m.info.days} days` : ''}</dd>
                    <dt>Threshold</dt><dd className="num">{m.threshold.toFixed(3)} <span className="muted">(chosen on validation data)</span></dd>
                    <dt>Values modelled</dt><dd>{m.info.features.map((f) => <code key={f} style={{ marginRight: 4 }}>{f}</code>)}</dd>
                    <dt>Context</dt><dd>{m.info.context.length ? m.info.context.map((f) => <code key={f} style={{ marginRight: 4 }}>{f}</code>) : '—'}</dd>
                    {m.info.segments && <><dt>Segments</dt><dd>{m.info.segments.length}</dd></>}
                    <dt>Trained</dt><dd>{fmt.date(m.created_at)}</dd>
                  </dl>
                </div>
              ))}
            </div>
            {d.notes.map((n) => <div key={n} className="notice">{n}</div>)}
            <p className="small muted">How these detectors were chosen, and how they compare with statistical baselines, is on the <Link to="/app/model">Model evidence</Link> page.</p>
            {me?.role === 'admin' && <div><button className="btn danger sm" onClick={remove}>Delete dataset</button></div>}
          </div>
        )}
      </div>
      {uploading && <UploadVersion id={d.id} onClose={() => setUploading(false)} onDone={() => { setUploading(false); setParams({}); ds.reload() }} />}
    </div>
  )
}
