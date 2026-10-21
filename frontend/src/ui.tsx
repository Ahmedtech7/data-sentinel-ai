import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Run, Severity } from './api'

// ---------- formatting
export const fmt = {
  int: (n: number | null | undefined) => (n == null ? '–' : Math.round(n).toLocaleString('en-US')),
  num: (n: number | null | undefined, d = 2) =>
    n == null ? '–' : Math.abs(n) >= 1000 ? n.toLocaleString('en-US', { maximumFractionDigits: 0 }) : n.toLocaleString('en-US', { maximumFractionDigits: d }),
  pct: (n: number | null | undefined, d = 1) => (n == null ? '–' : `${(n * 100).toFixed(d)}%`),
  bytes: (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`),
  date: (s: string) => new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z').toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
  day: (s: string) => new Date(s + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  ago: (s: string) => {
    const t = new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z').getTime()
    const m = Math.round((Date.now() - t) / 60000)
    if (m < 1) return 'just now'
    if (m < 60) return `${m} min ago`
    if (m < 1440) return `${Math.round(m / 60)} h ago`
    return `${Math.round(m / 1440)} d ago`
  },
  detector: (d: string) =>
    ({
      residual_z_calibrated: 'Context model · calibrated deviation',
      residual_z_max: 'Context model · max deviation',
      robust_z_learned: 'Robust z-score · learned feed features',
      lof_learned: 'Local Outlier Factor · learned feed features',
      isolation_forest_learned: 'Isolation Forest · learned feed features',
      ensemble_learned: 'Ensemble (max-z + LOF) · learned feed features',
      lof_residual: 'Local Outlier Factor · context model',
      isolation_forest_residual: 'Isolation Forest · context model',
      isolation_forest_group: 'Isolation Forest · group z-scores',
      ensemble_residual: 'Ensemble (max-z + LOF) · context model',
      robust_z_group: 'Robust z-score · per group',
      robust_z_seasonal: 'Robust z-score · weekday-adjusted, raw',
      zscore_global: 'z-score · global',
      zscore_raw: 'z-score · per segment',
      iqr_global: 'IQR fences · global',
    })[d] ?? d,
}

// ---------- brand
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs><linearGradient id="ds-logo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#4f46e5" /><stop offset=".6" stopColor="#0ea5e9" /><stop offset="1" stopColor="#14b8a6" /></linearGradient></defs>
      <rect width="32" height="32" rx="8" fill="url(#ds-logo)" />
      <path d="M7 16h5l2.5-6 3 12 2.5-6H25" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="25" cy="16" r="2.2" fill="#fde68a" />
    </svg>
  )
}

// ---------- severity: shape + colour + word
const SEV: Record<Severity, { color: string; shape: ReactNode }> = {
  critical: { color: 'var(--critical)', shape: <path d="M6 0.8 11.2 6 6 11.2 0.8 6Z" /> },
  high: { color: 'var(--serious)', shape: <path d="M6 1 11.3 10.5H0.7Z" /> },
  medium: { color: 'var(--warning)', shape: <circle cx="6" cy="6" r="4.6" /> },
  low: { color: 'var(--muted)', shape: <circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.8" stroke="currentColor" /> },
}
export function Sev({ s, label = true }: { s: Severity; label?: boolean }) {
  return (
    <span className="sev" title={`${s} severity`}>
      <svg width="12" height="12" viewBox="0 0 12 12" fill={SEV[s].color} style={{ color: SEV[s].color }} aria-hidden="true">
        {SEV[s].shape}
      </svg>
      {label ? s : <span className="sr-only">{s}</span>}
    </span>
  )
}
export const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low']

export function RunStatus({ run }: { run: Run | null }) {
  if (!run) return <span className="pill">no runs</span>
  if (run.status === 'queued') return <span className="pill"><span className="spinner" /> queued</span>
  if (run.status === 'running') return <span className="pill accent"><span className="spinner" /> {run.stage || 'running'}</span>
  if (run.status === 'failed') return <span className="pill" style={{ color: 'var(--crit-ink)' }} title={run.error ?? ''}>failed</span>
  const f = run.summary?.findings
  const worst = f && SEVERITIES.find((s) => f[s] > 0)
  return worst ? (
    <span className="pill"><Sev s={worst} label={false} /> {Object.values(f!).reduce((a, b) => a + b, 0)} findings</span>
  ) : (
    <span className="pill" style={{ color: 'var(--good-ink)' }}>✓ clean</span>
  )
}

export const isBusy = (r: Run | null | undefined) => !!r && (r.status === 'queued' || r.status === 'running')

// ---------- states
export function Skeleton({ h = 120, w = '100%' }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} aria-hidden="true" />
}
export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={i === 0 ? 64 : 160} />)}
    </div>
  )
}
export function ErrorBox({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div className="error row" role="alert">
      <span className="grow">{error}</span>
      {retry && <button className="btn sm" onClick={retry}>Retry</button>}
    </div>
  )
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  )
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="card-head">
          <h3 className="grow">{title}</h3>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="card-body">{children}</div>
      </div>
    </div>
  )
}

export function FileDrop({ file, onFile }: { file: File | null; onFile: (f: File) => void }) {
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  return (
    <div
      className={`drop ${over ? 'over' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f) }}
    >
      <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      {file ? (
        <div><b>{file.name}</b> <span className="muted">· {fmt.bytes(file.size)}</span><div className="small muted">Click to choose a different file</div></div>
      ) : (
        <div><b>Drop a CSV here</b> or click to browse<div className="small muted">UTF-8, header row, up to 15 MB</div></div>
      )}
    </div>
  )
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card tile">
      <div className="eyebrow">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}
