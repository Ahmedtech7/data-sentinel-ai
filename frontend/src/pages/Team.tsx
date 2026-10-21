import { useState } from 'react'
import { api, useApi, type Member, type Role } from '../api'
import { useAuth } from '../App'
import { ErrorBox, Loading } from '../ui'

const ROLE_HELP: Record<Role, string> = {
  viewer: 'Reads datasets, analyses and incidents.',
  analyst: 'Also uploads versions, edits rules, runs Fault Lab and manages incidents.',
  admin: 'Also manages members and deletes datasets.',
}

export default function Team() {
  const { me } = useAuth()
  const list = useApi<Member[]>('/api/auth/members')
  const [error, setError] = useState<string | null>(null)
  const isAdmin = me?.role === 'admin'
  const setRole = async (id: number, role: string) => {
    try {
      await api(`/api/auth/members/${id}?role=${role}`, { method: 'PATCH' })
      list.reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head"><div className="grow"><h1>Team</h1><p className="lede">{me?.organization.name}. Everyone here sees only this workspace’s data.</p></div></div>
      <div className="split">
        <div className="card table-wrap">
          {list.error ? <ErrorBox error={list.error} /> : !list.data ? <div className="card-body"><Loading rows={1} /></div> : (
            <table><thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
              <tbody>{list.data.map((m) => (
                <tr key={m.id}><td><b>{m.name}</b>{m.id === me?.id && <span className="muted"> (you)</span>}</td><td className="small">{m.email}</td>
                  <td>{isAdmin && m.id !== me?.id ? (
                    <select aria-label={`Role for ${m.name}`} value={m.role} onChange={(e) => setRole(m.id, e.target.value)} style={{ width: 130 }}>
                      {(['viewer', 'analyst', 'admin'] as Role[]).map((r) => <option key={r}>{r}</option>)}
                    </select>) : m.role}</td></tr>
              ))}</tbody></table>
          )}
        </div>
        <div className="card pad stack">
          <h3>Roles</h3>
          {(Object.keys(ROLE_HELP) as Role[]).map((r) => <div key={r} className="small"><b>{r}</b> · <span className="ink2">{ROLE_HELP[r]}</span></div>)}
          <p className="small muted">Your workspace is private to this browser and is kept for 24 hours.</p>
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    </div>
  )
}
