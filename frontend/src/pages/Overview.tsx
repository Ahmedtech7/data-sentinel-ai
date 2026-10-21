import { Link, useNavigate } from 'react-router-dom'
import { useApi, type Incident, type Run, type Severity } from '../api'
import { useAuth } from '../App'
import { Empty, ErrorBox, fmt, isBusy, Loading, RunStatus, Sev, SEVERITIES, Stat } from '../ui'

interface OverviewData {
  datasets: number; active_incidents: Record<Severity, number>; incidents_total: number; opened_last_30d: number
  resolved: number; mean_hours_to_resolve: number | null; running: number
  recent_runs: (Run & { dataset: string; dataset_id: number })[]
}

export default function Overview() {
  const { me } = useAuth()
  const nav = useNavigate()
  const o = useApi<OverviewData>('/api/overview', (d) => d.running > 0, 2500)
  const inc = useApi<Incident[]>(`/api/incidents?status=active&_=${o.data?.running ?? 0}`)
  if (o.error) return <ErrorBox error={o.error} retry={o.reload} />
  if (!o.data) return <Loading />
  const d = o.data
  const active = SEVERITIES.reduce((a, s) => a + d.active_incidents[s], 0)
  const rank = (s: Severity) => SEVERITIES.indexOf(s)
  const top = [...(inc.data ?? [])].sort((a, b) => rank(a.severity) - rank(b.severity) || b.id - a.id).slice(0, 6)

  return (
    <div className="stack" style={{ gap: 24 }}>
      <div className="page-head">
        <div className="grow">
          <div className="eyebrow">{me?.organization.name}</div>
          <h1>Watchtower</h1>
          <p className="lede">What changed in your data, what broke, and what still needs a decision.</p>
        </div>
        <Link className="btn primary" to="/app/datasets">Monitor a dataset</Link>
      </div>

      {d.running > 0 && (
        <div className="banner scan" role="status"><span className="spinner" /> {d.running} analysis{d.running > 1 ? 'es' : ''} running — results appear here automatically.</div>
      )}

      <div className="grid g4">
        <Stat label="Active incidents" value={active} sub={
          <span className="row wrap" style={{ gap: 12 }}>
            {SEVERITIES.filter((s) => d.active_incidents[s]).map((s) => <span key={s}><Sev s={s} label={false} /> {d.active_incidents[s]} {s}</span>)}
            {!active && 'Nothing open'}
          </span>} />
        <Stat label="Opened · last 30 days" value={d.opened_last_30d} sub={`${d.incidents_total} incidents all-time`} />
        <Stat label="Resolved" value={d.resolved} sub={d.mean_hours_to_resolve != null ? `mean time to resolve ${d.mean_hours_to_resolve < 1 ? Math.round(d.mean_hours_to_resolve * 60) + ' min' : d.mean_hours_to_resolve.toFixed(1) + ' h'}` : 'no resolutions yet'} />
        <Stat label="Monitored datasets" value={d.datasets} sub={d.running ? `${d.running} running now` : 'all analyses complete'} />
      </div>

      <div className="grid g2">
        <div className="card">
          <div className="card-head"><h3 className="grow">Needs attention</h3><Link className="small" to="/app/incidents">All incidents →</Link></div>
          {inc.loading && !inc.data ? <div className="card-body"><Loading rows={1} /></div> : top.length ? (
            <div className="table-wrap"><table><tbody>
              {top.map((i) => (
                <tr key={i.id} className="link" onClick={() => nav(`/app/incidents/${i.id}`)}>
                  <td style={{ width: 92 }}><Sev s={i.severity} /></td>
                  <td><Link to={`/app/incidents/${i.id}`} style={{ textDecoration: 'none' }}><b>{i.title}</b></Link><div className="small muted">{i.dataset} · {i.status} · {fmt.ago(i.last_seen_at)}</div></td>
                </tr>
              ))}
            </tbody></table></div>
          ) : d.running ? <Empty title="Analysing…">Findings will appear here as soon as the running analyses finish.</Empty>
            : <Empty title="All clear">No open incidents. New problems will appear here when an analysis finds them.</Empty>}
        </div>

        <div className="card">
          <div className="card-head"><h3 className="grow">Recent analyses</h3></div>
          {d.recent_runs.length ? (
            <div className="table-wrap"><table><tbody>
              {d.recent_runs.map((r) => (
                <tr key={r.id} className="link" onClick={() => nav(`/app/datasets/${r.dataset_id}?version=${r.version_id}`)}>
                  <td><b>{r.dataset}</b><div className="small muted">{r.summary ? `version ${r.summary.version} · ${fmt.int(r.summary.rows)} rows` : `run #${r.id}`} · {fmt.ago(r.created_at)}</div></td>
                  <td style={{ textAlign: 'right' }}><RunStatus run={r} />{isBusy(r) ? null : r.summary && <div className="small muted">{r.summary.checks_passed}/{r.summary.checks_total} checks passed</div>}</td>
                </tr>
              ))}
            </tbody></table></div>
          ) : <Empty title="No data yet">Upload a CSV on the <Link to="/app/datasets">Datasets</Link> page to start monitoring.</Empty>}
        </div>
      </div>
    </div>
  )
}
