import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useApi, type DatasetRow, type Incident } from '../api'
import { Empty, ErrorBox, fmt, Loading, Sev, SEVERITIES } from '../ui'

const STATUS = [['active', 'Active'], ['resolved', 'Resolved'], ['dismissed', 'Dismissed'], ['', 'All']] as const

export default function Incidents() {
  const [p, setP] = useSearchParams()
  const nav = useNavigate()
  const status = p.get('status') ?? 'active'
  const q = new URLSearchParams()
  if (status) q.set('status', status)
  if (p.get('severity')) q.set('severity', p.get('severity')!)
  if (p.get('dataset')) q.set('dataset_id', p.get('dataset')!)
  const list = useApi<Incident[]>(`/api/incidents?${q}`)
  const datasets = useApi<DatasetRow[]>('/api/datasets')
  const set = (k: string, v: string) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); if (k === 'status' && !v) n.set('status', ''); setP(n) }

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head"><div className="grow"><h1>Incidents</h1><p className="lede">One incident per root cause. A problem that recurs in a later version is added to the open incident instead of opening a new one.</p></div></div>
      <div className="row wrap">
        <div className="tabs" style={{ marginBottom: 0, borderBottom: 0 }}>
          {STATUS.map(([v, l]) => <button key={l} className={status === v ? 'on' : ''} onClick={() => set('status', v)}>{l}</button>)}
        </div>
        <span className="grow" />
        <select aria-label="Severity" style={{ width: 150 }} value={p.get('severity') ?? ''} onChange={(e) => set('severity', e.target.value)}>
          <option value="">All severities</option>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select aria-label="Dataset" style={{ width: 240 }} value={p.get('dataset') ?? ''} onChange={(e) => set('dataset', e.target.value)}>
          <option value="">All datasets</option>{datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </div>
      {list.error && <ErrorBox error={list.error} retry={list.reload} />}
      {!list.data ? !list.error && <Loading rows={2} /> : !list.data.length ? (
        <div className="card"><Empty title={status === 'active' ? 'No active incidents' : 'No incidents here'}>Try another filter, or break some data on purpose in the <Link to="/app/lab">Fault Lab</Link>.</Empty></div>
      ) : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Severity</th><th>Incident</th><th className="hide-sm">Status</th><th className="num hide-sm">Seen</th><th className="hide-sm">Assignee</th><th className="hide-sm">Last seen</th></tr></thead>
          <tbody>{list.data.map((i) => (
            <tr key={i.id} className="link" onClick={() => nav(`/app/incidents/${i.id}`)}>
              <td><Sev s={i.severity} /></td>
              <td><Link to={`/app/incidents/${i.id}`} style={{ textDecoration: 'none' }}><b>{i.title}</b></Link><div className="small muted">#{i.id} · {i.dataset} · {i.kind.replace('_', ' ')}</div></td>
              <td className="hide-sm"><span className="pill">{i.status}</span></td>
              <td className="num hide-sm">{i.occurrences}×</td>
              <td className="small hide-sm">{i.assignee ?? <span className="muted">—</span>}</td>
              <td className="small muted hide-sm">{fmt.ago(i.last_seen_at)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  )
}
