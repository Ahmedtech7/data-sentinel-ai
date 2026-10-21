import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, useApi, type DatasetRow } from '../api'
import { can, useAuth } from '../App'
import { Empty, ErrorBox, FileDrop, fmt, isBusy, Loading, Modal, RunStatus } from '../ui'

export function NewDataset({ onClose }: { onClose: () => void }) {
  const nav = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async () => {
    if (!file) return setError('Choose a CSV file first.')
    setBusy(true)
    setError(null)
    const body = new FormData()
    body.set('name', name.trim() || file.name.replace(/\.(csv|txt)$/i, ''))
    body.set('file', file)
    try {
      const r = await api<{ id: number }>('/api/datasets', { method: 'POST', body })
      nav(`/app/datasets/${r.id}`)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }
  return (
    <Modal title="Monitor a new dataset" onClose={onClose}>
      <div className="stack">
        <p className="ink2 small">The first upload becomes the <b>baseline</b>: Data Sentinel infers the schema and a data contract from it and trains the models. Later uploads are checked against it.</p>
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sales orders — EU warehouse" maxLength={120} /></label>
        <FileDrop file={file} onFile={(f) => { setFile(f); setError(null) }} />
        {error && <div className="error" role="alert">{error}</div>}
        <div className="row"><span className="grow small muted">Files are stored compressed in your workspace only.</span>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={submit} disabled={busy}>{busy ? <><span className="spinner" /> Uploading…</> : 'Upload & analyse'}</button>
        </div>
      </div>
    </Modal>
  )
}

export default function Datasets() {
  const { me } = useAuth()
  const nav = useNavigate()
  const [adding, setAdding] = useState(false)
  const list = useApi<DatasetRow[]>('/api/datasets', (d) => d.some((x) => isBusy(x.latest_run)))
  return (
    <div className="stack" style={{ gap: 24 }}>
      <div className="page-head">
        <div className="grow"><h1>Datasets</h1><p className="lede">Each dataset is a feed you upload repeatedly. Every version is checked against the baseline’s contract and models.</p></div>
        {can(me, 'analyst') && <button className="btn primary" onClick={() => setAdding(true)}>New dataset</button>}
      </div>
      {list.error && <ErrorBox error={list.error} retry={list.reload} />}
      {!list.data ? !list.error && <Loading rows={2} /> : list.data.length === 0 ? (
        <div className="card"><Empty title="Nothing monitored yet">Upload any CSV with a header row. A timestamp column unlocks feed monitoring; numeric columns unlock the record model.
          {can(me, 'analyst') && <div style={{ marginTop: 16 }}><button className="btn primary" onClick={() => setAdding(true)}>Upload a CSV</button></div>}</Empty></div>
      ) : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Dataset</th><th className="num hide-sm">Versions</th><th>Latest analysis</th><th className="num hide-sm">Open incidents</th><th className="hide-sm">Updated</th></tr></thead>
          <tbody>
            {list.data.map((d) => (
              <tr key={d.id} className="link" onClick={() => nav(`/app/datasets/${d.id}`)}>
                <td><Link to={`/app/datasets/${d.id}`} style={{ textDecoration: 'none' }}><b>{d.name}</b></Link><div className="small muted ellipsis" style={{ maxWidth: 520 }}>{d.description || '—'}</div></td>
                <td className="num hide-sm">{d.versions}</td>
                <td><RunStatus run={d.latest_run} /></td>
                <td className="num hide-sm">{d.open_incidents}</td>
                <td className="small muted hide-sm">{fmt.ago(d.latest_run?.created_at ?? d.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {adding && <NewDataset onClose={() => setAdding(false)} />}
    </div>
  )
}
