import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, useApi, type IncidentDetail, type Member } from '../api'
import { can, useAuth } from '../App'
import { Evidence } from '../evidence'
import { ErrorBox, fmt, Loading, Sev, SEVERITIES } from '../ui'

const NEXT: Record<string, [string, string][]> = {
  open: [['investigating', 'Start investigating'], ['resolved', 'Resolve'], ['dismissed', 'Dismiss (expected change)']],
  investigating: [['resolved', 'Resolve'], ['dismissed', 'Dismiss (expected change)'], ['open', 'Back to open']],
  resolved: [['open', 'Reopen']],
  dismissed: [['open', 'Reopen']],
}

export default function IncidentPage() {
  const { id } = useParams()
  const { me } = useAuth()
  const inc = useApi<IncidentDetail>(`/api/incidents/${id}`)
  const members = useApi<Member[]>('/api/auth/members')
  const [note, setNote] = useState('')
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (inc.error) return <ErrorBox error={inc.error} retry={inc.reload} />
  if (!inc.data) return <Loading />
  const i = inc.data
  const editable = can(me, 'analyst')

  const act = async (path: string, method: string, json: unknown) => {
    setBusy(true)
    setError(null)
    try {
      inc.setData(await api<IncidentDetail>(path, { method, json }))
      return true
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head">
        <div className="grow">
          <div className="crumbs"><Link to="/app/incidents">Incidents</Link> / #{i.id}</div>
          <div className="row" style={{ marginBottom: 8 }}><Sev s={i.severity} /><span className="pill">{i.status}</span><span className="pill">{i.kind.replace('_', ' ')}</span></div>
          <h1 style={{ fontSize: 34 }}>{i.title}</h1>
          <p className="lede">{i.summary}</p>
        </div>
      </div>

      <div className="split">
        <div className="stack" style={{ minWidth: 0 }}>
          <div className="card">
            <div className="card-head"><h3 className="grow">Evidence</h3><Link className="small" to={`/app/datasets/${i.dataset_id}`}>{i.dataset} →</Link></div>
            <div className="card-body"><Evidence kind={i.kind} evidence={i.evidence} /></div>
          </div>
          <div className="card">
            <div className="card-head"><h3>Timeline</h3></div>
            <div className="card-body">
              <div className="timeline">
                {i.events.map((e) => (
                  <div key={e.id} className="ev">
                    <span className={`mark ${e.type === 'comment' ? '' : 'on'}`} aria-hidden="true" />
                    <div>
                      <div className="small muted">{e.user ?? 'Data Sentinel'} · {fmt.date(e.created_at)}</div>
                      <div style={e.type === 'comment' ? { background: 'var(--surface-2)', padding: '8px 10px', borderRadius: 8, marginTop: 4, whiteSpace: 'pre-wrap' } : undefined}>{e.body}</div>
                    </div>
                  </div>
                ))}
              </div>
              {editable && (
                <form className="stack-sm" onSubmit={async (e) => { e.preventDefault(); if (comment.trim() && await act(`/api/incidents/${i.id}/comments`, 'POST', { body: comment })) setComment('') }}>
                  <textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a note: what you checked, who you contacted, the root cause…" maxLength={4000} aria-label="Comment" />
                  <div><button className="btn sm" disabled={busy || !comment.trim()}>Comment</button></div>
                </form>
              )}
            </div>
          </div>
        </div>

        <div className="stack sticky" style={{ position: 'sticky', top: 20 }}>
          <div className="card pad stack">
            <dl className="reason">
              <dt>Dataset</dt><dd>{i.dataset}</dd>
              <dt>Opened</dt><dd>{fmt.date(i.opened_at)}</dd>
              <dt>Last seen</dt><dd>{fmt.date(i.last_seen_at)}</dd>
              <dt>Occurrences</dt><dd>{i.occurrences} version{i.occurrences > 1 ? 's' : ''}</dd>
              {i.resolved_at && <><dt>Closed</dt><dd>{fmt.date(i.resolved_at)}</dd></>}
            </dl>
            {editable ? (
              <>
                <hr className="divider" />
                <label className="field">Assignee
                  <select value={i.assignee_id ?? ''} disabled={busy} onChange={(e) => act(`/api/incidents/${i.id}`, 'PATCH', { assignee_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">Unassigned</option>{members.data?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select></label>
                <label className="field">Severity
                  <select value={i.severity} disabled={busy} onChange={(e) => act(`/api/incidents/${i.id}`, 'PATCH', { severity: e.target.value })}>
                    {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select></label>
                <label className="field">Resolution note <span className="hint">optional, saved with the status change</span>
                  <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. ETL retry fixed upstream" maxLength={2000} /></label>
                <div className="stack-sm">
                  {NEXT[i.status].map(([s, l], k) => (
                    <button key={s} className={`btn ${k === 0 ? 'primary' : ''}`} disabled={busy} onClick={async () => { if (await act(`/api/incidents/${i.id}`, 'PATCH', { status: s, note })) setNote('') }}>{l}</button>
                  ))}
                </div>
              </>
            ) : <p className="small muted">Viewers can follow incidents; analysts and admins can change them.</p>}
            {error && <div className="error">{error}</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
