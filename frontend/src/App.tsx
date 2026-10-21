import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, useApi, type Me } from './api'
import { Loading, Logo } from './ui'
import Landing from './pages/Landing'
import { Login, Register } from './pages/Auth'

const Overview = lazy(() => import('./pages/Overview'))
const Datasets = lazy(() => import('./pages/Datasets'))
const DatasetPage = lazy(() => import('./pages/DatasetPage'))
const Incidents = lazy(() => import('./pages/Incidents'))
const IncidentPage = lazy(() => import('./pages/IncidentPage'))
const Lab = lazy(() => import('./pages/Lab'))
const ModelPage = lazy(() => import('./pages/ModelPage'))
const Team = lazy(() => import('./pages/Team'))

interface AuthCtx { me: Me | null; ready: boolean; setMe: (m: Me | null) => void; signOut: () => Promise<void> }
const Auth = createContext<AuthCtx>(null!)
export const useAuth = () => useContext(Auth)
const ORDER = ['viewer', 'analyst', 'admin']
export const can = (me: Me | null, role: 'analyst' | 'admin') => !!me && ORDER.indexOf(me.role) >= ORDER.indexOf(role)

function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [ready, setReady] = useState(false)
  useEffect(() => {
    api<Me>('/api/auth/me').then(setMe).catch(() => setMe(null)).finally(() => setReady(true))
    const out = () => setMe(null)
    window.addEventListener('sentinel:signed-out', out)
    return () => window.removeEventListener('sentinel:signed-out', out)
  }, [])
  const signOut = useCallback(async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {})
    setMe(null)
  }, [])
  return <Auth.Provider value={{ me, ready, setMe, signOut }}>{children}</Auth.Provider>
}

const NAV = [
  { to: '/app', label: 'Overview', end: true, icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { to: '/app/datasets', label: 'Datasets', icon: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3' },
  { to: '/app/incidents', label: 'Incidents', icon: 'M12 3 2 20h20L12 3Zm0 6v5m0 3v.5' },
  { to: '/app/lab', label: 'Fault Lab', icon: 'M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3M7 15h10' },
  { to: '/app/model', label: 'Model evidence', icon: 'M4 20V10m6 10V4m6 16v-7m6 7H2' },
  { to: '/app/team', label: 'Team', icon: 'M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1m7-9a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm13 9v-1a4 4 0 0 0-3-3.9M16 3.1a3.5 3.5 0 0 1 0 6.8' },
]

function Shell() {
  const { me, ready, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const nav = useNavigate()
  const active = useApi<{ id: number }[]>(me ? `/api/incidents?status=active&_=${loc.pathname}` : null)
  useEffect(() => setOpen(false), [loc.pathname])
  if (!ready) return <div className="main"><Loading /></div>
  if (!me) return <Navigate to="/login" state={{ from: loc.pathname }} replace />
  return (
    <div className="shell">
      <aside className={`side ${open ? 'open' : ''}`} aria-label="Main navigation">
        <NavLink to="/app" className="brand"><Logo /><b>DATA SENTINEL</b></NavLink>
        <nav className="nav stack-sm" style={{ gap: 2 }}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={n.icon} /></svg>
              {n.label}
              {n.label === 'Incidents' && !!active.data?.length && <span className="count" aria-label={`${active.data.length} active`}>{active.data.length}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          {me.organization.is_demo && <span className="pill accent" title="This workspace is temporary and is deleted after 24 hours">Demo workspace · expires in 24 h</span>}
          <div className="small">
            <div className="ellipsis"><b>{me.name}</b></div>
            <div className="muted ellipsis">{me.organization.name} · {me.role}</div>
          </div>
          <button className="btn sm" onClick={async () => { await signOut(); nav('/') }}>Sign out</button>
        </div>
      </aside>
      <div style={{ minWidth: 0 }}>
        <div className="topbar">
          <button className="btn sm" onClick={() => setOpen(!open)} aria-label="Open navigation">☰</button>
          <Logo size={22} /><b className="mono" style={{ letterSpacing: '.14em', fontSize: 12 }}>DATA SENTINEL</b>
        </div>
        <main className="main">
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
        </main>
      </div>
      {open && <div className="modal-bg" style={{ zIndex: 45 }} onClick={() => setOpen(false)} />}
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/app" element={<Shell />}>
            <Route index element={<Overview />} />
            <Route path="datasets" element={<Datasets />} />
            <Route path="datasets/:id" element={<DatasetPage />} />
            <Route path="incidents" element={<Incidents />} />
            <Route path="incidents/:id" element={<IncidentPage />} />
            <Route path="lab" element={<Lab />} />
            <Route path="model" element={<ModelPage />} />
            <Route path="team" element={<Team />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
