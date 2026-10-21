import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api, type Me } from '../api'
import { useAuth } from '../App'
import { Logo } from '../ui'
import { useStartDemo } from './Landing'

function Frame({ title, children, foot }: { title: string; children: ReactNode; foot: ReactNode }) {
  const demo = useStartDemo()
  return (
    <div style={{ minHeight: '100%', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div style={{ width: 'min(420px, 100%)' }} className="stack">
        <Link to="/" className="brand" style={{ justifyContent: 'center' }}><Logo /><b>DATA SENTINEL</b></Link>
        <div className="card pad stack">
          <h1 className="display" style={{ fontSize: 34 }}>{title}</h1>
          {children}
        </div>
        <div className="small muted" style={{ textAlign: 'center' }}>{foot}</div>
        <button className="btn ghost" onClick={demo.start} disabled={demo.busy}>
          {demo.busy ? <><span className="spinner" /> Preparing demo…</> : 'Or explore a live demo workspace →'}
        </button>
        {demo.error && <div className="error">{demo.error}</div>}
      </div>
    </div>
  )
}

function useSubmit(path: string) {
  const { setMe } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const json = Object.fromEntries(new FormData(e.currentTarget))
      setMe(await api<Me>(path, { method: 'POST', json }))
      nav((loc.state as { from?: string })?.from ?? '/app')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return { submit, error, busy }
}

export function Login() {
  const { me } = useAuth()
  const f = useSubmit('/api/auth/login')
  if (me) return <Navigate to="/app" replace />
  return (
    <Frame title="Sign in" foot={<>New here? <Link to="/register">Create an account</Link></>}>
      <form className="stack" onSubmit={f.submit}>
        <label className="field">Email<input name="email" type="email" autoComplete="email" required /></label>
        <label className="field">Password<input name="password" type="password" autoComplete="current-password" required /></label>
        {f.error && <div className="error" role="alert">{f.error}</div>}
        <button className="btn primary" disabled={f.busy}>{f.busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </Frame>
  )
}

export function Register() {
  const { me } = useAuth()
  const f = useSubmit('/api/auth/register')
  if (me) return <Navigate to="/app" replace />
  return (
    <Frame title="Create a workspace" foot={<>Already have an account? <Link to="/login">Sign in</Link></>}>
      <form className="stack" onSubmit={f.submit}>
        <label className="field">Your name<input name="name" autoComplete="name" required maxLength={120} /></label>
        <label className="field">Organization<input name="organization" autoComplete="organization" required maxLength={120} /></label>
        <label className="field">Work email<input name="email" type="email" autoComplete="email" required /></label>
        <label className="field">Password <span className="hint">At least 10 characters</span>
          <input name="password" type="password" autoComplete="new-password" minLength={10} required /></label>
        {f.error && <div className="error" role="alert">{f.error}</div>}
        <button className="btn primary" disabled={f.busy}>{f.busy ? 'Creating…' : 'Create workspace'}</button>
      </form>
    </Frame>
  )
}
