import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, useApi, type Me } from '../api'
import { useAuth } from '../App'
import { fmt, Logo } from '../ui'
import type { Report } from './ModelPage'

export function useStartDemo() {
  const { setMe } = useAuth()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      setMe(await api<Me>('/api/auth/demo', { method: 'POST' }))
      nav('/app')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return { start, busy, error }
}

function HeroTrace() {
  // Illustration of an alert: a daily-volume trace with an unexplained dip.
  const pts = [31, 29, 33, 30, 34, 28, 30, 32, 29, 31, 5, 7, 30, 33, 31, 29, 32, 30]
  const x = (i: number) => 20 + i * 30
  const y = (v: number) => 150 - v * 3.4
  const d = pts.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ')
  return (
    <svg viewBox="0 0 560 190" width="100%" role="img" aria-label="Illustration: rows per day for one segment, with a two-day drop flagged">
      {[40, 80, 120].map((g) => <line key={g} x1="20" x2="530" y1={g} y2={g} stroke="var(--line)" />)}
      <path d={pts.map((_, i) => `${i ? 'L' : 'M'}${x(i)},${y(30)}`).join(' ')} fill="none" stroke="var(--expected)" strokeWidth="2" strokeDasharray="5 5" />
      <rect x={x(10) - 12} y="20" width="54" height="140" rx="6" fill="color-mix(in srgb, var(--critical) 9%, transparent)" />
      <path d={d} fill="none" stroke="var(--series-1)" strokeWidth="2.4" strokeLinejoin="round" />
      {[10, 11].map((i) => <circle key={i} cx={x(i)} cy={y(pts[i])} r="5" fill="var(--critical)" stroke="var(--surface)" strokeWidth="2" />)}
      <g transform="translate(334, 104)">
        <rect width="214" height="54" rx="8" fill="var(--surface)" stroke="var(--line-strong)" />
        <text x="12" y="22" fontSize="12" fontWeight="600" fill="var(--ink)" fontFamily="var(--sans)">web · EMEA — volume drop</text>
        <text x="12" y="40" fontSize="11.5" fill="var(--ink-2)" fontFamily="var(--mono)">5 rows vs 29 expected (−83%)</text>
      </g>
    </svg>
  )
}

export default function Landing() {
  const { me } = useAuth()
  const demo = useStartDemo()
  const report = useApi<Report>('/api/model/report')
  const r = report.data?.levels
  const row = r?.row.candidates.find((c) => c.detector === r.row.selected)
  const rowStat = r?.row.candidates.find((c) => c.detector === r.row.best_statistical)
  const feed = r?.period.shipped

  return (
    <div style={{ minHeight: '100%' }}>
      <div className="hero-bg">
      <header className="row" style={{ maxWidth: 1180, margin: '0 auto', padding: '18px 24px' }}>
        <Link to="/" className="brand" style={{ padding: 0 }}><Logo /><b>DATA SENTINEL</b></Link>
        <nav className="row right" style={{ gap: 6 }}>
          <a className="btn ghost sm" href="#how">How it works</a>
          <a className="btn ghost sm" href="#evidence">Evidence</a>
          {me && <Link className="btn primary sm" to="/app">Open workspace</Link>}
        </nav>
      </header>

      <section style={{ maxWidth: 1180, margin: '0 auto', padding: '48px 24px 56px' }} className="grid g2">
        <div className="stack" style={{ alignContent: 'center', gap: 22 }}>
          <span className="eyebrow">Data quality monitoring for business feeds</span>
          <h1 className="display" style={{ fontSize: 'clamp(44px, 6vw, 76px)' }}>
            Bad data doesn’t announce itself. <em className="grad-text">Sentinel does.</em>
          </h1>
          <p className="ink2" style={{ fontSize: 17, maxWidth: 56 + 'ch' }}>
            Upload a CSV feed. Data Sentinel learns what normal looks like, checks every new version against it, and
            opens an incident when something breaks — with the rows, the expected values and the baseline that proves it.
          </p>
          <div className="row wrap">
            <button className="btn accent lg" onClick={demo.start} disabled={demo.busy}>
              {demo.busy ? <><span className="spinner" /> Preparing your workspace…</> : 'Open your workspace'}
            </button>
          </div>
          {demo.error && <div className="error">{demo.error}</div>}
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="pill accent">Private workspace</span>
            <span className="pill tone-sky">Sample retail data included</span>
            <span className="pill tone-teal">No sign-up needed</span>
          </div>
        </div>
        <div className="card pad" style={{ alignSelf: 'center', boxShadow: 'var(--shadow)' }}>
          <div className="row between" style={{ marginBottom: 10 }}>
            <span className="eyebrow">Example alert</span>
            <span className="sev"><svg width="12" height="12" viewBox="0 0 12 12" fill="var(--serious)" aria-hidden="true"><path d="M6 1 11.3 10.5H0.7Z" /></svg>high</span>
          </div>
          <HeroTrace />
          <div className="legend" style={{ marginTop: 6 }}>
            <span><i className="swatch-line" style={{ background: 'var(--series-1)' }} /> rows received</span>
            <span><i className="swatch-dash" /> expected for that weekday</span>
          </div>
        </div>
      </section>
      </div>

      <section id="how" style={{ borderTop: '1px solid var(--line)', background: 'var(--surface)' }}>
        <div style={{ maxWidth: 1180, margin: '0 auto', padding: '56px 24px' }} className="stack">
          <span className="eyebrow" style={{ color: 'var(--accent)' }}>Three layers, each where it belongs</span>
          <h2 className="display" style={{ fontSize: 40, maxWidth: '22ch' }}>Rules where rules are exact. Models where they are not.</h2>
          <div className="grid g3" style={{ marginTop: 12 }}>
            {[
              ['01 · Contract', 'Deterministic checks', 'A data contract is inferred from your baseline: types, required columns, allowed values, ranges, unique keys, plus formulas you add (revenue = qty × price). Exact, explainable, zero false alarms on clean data.'],
              ['02 · Records', 'Context model', 'Gradient-boosted models learn what each value should be for its context — this SKU, this channel, this region — and how much it normally varies. A $3,404 line for a $34 product stands out even though $3,404 is a normal price elsewhere.'],
              ['03 · Feed', 'Health model', 'Every segment-day is scored on volume vs. the same weekday, completeness, duplication, ingestion delay, and the level of each numeric column adjusted for that day’s product mix by the context model, plus a Poisson volume test.'],
            ].map(([k, t, d]) => (
              <div key={k} className="card pad stack-sm layer">
                <div className="row"><span className="chip">{k.slice(0, 2)}</span><span className="eyebrow">{k.slice(5)}</span></div>
                <h3 style={{ fontSize: 20 }}>{t}</h3>
                <p className="ink2">{d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="evidence" style={{ maxWidth: 1180, margin: '0 auto', padding: '56px 24px' }} className="stack">
        <span className="eyebrow" style={{ color: 'var(--accent)' }}>Measured, not claimed</span>
        <h2 className="display" style={{ fontSize: 40, maxWidth: '24ch' }}>Every number below comes from held-out test data.</h2>
        <p className="ink2" style={{ maxWidth: '70ch' }}>
          Models and thresholds were chosen on validation streams only, then scored once on five independent synthetic companies with labelled, injected faults.
          Statistical baselines were evaluated on exactly the same data.
        </p>
        {report.error && <div className="notice">Evaluation report unavailable: {report.error}</div>}
        {r && row && rowStat && feed && (
          <div className="grid g4">
            <div className="card tile"><div className="eyebrow">Records · average precision</div><div className="value">{row.test.average_precision.toFixed(2)}</div><div className="sub">vs {rowStat.test.average_precision.toFixed(2)} for the best statistical baseline ({fmt.detector(rowStat.detector)})</div></div>
            <div className="card tile"><div className="eyebrow">Records · precision / recall</div><div className="value">{fmt.pct(row.test.precision, 0)} / {fmt.pct(row.test.recall, 0)}</div><div className="sub">false-positive rate {fmt.pct(row.test.fpr, 2)}</div></div>
            <div className="card tile"><div className="eyebrow">Feed · precision / recall</div><div className="value">{fmt.pct(feed.test.precision, 0)} / {fmt.pct(feed.test.recall, 0)}</div><div className="sub">false-positive rate {fmt.pct(feed.test.fpr, 1)} per segment-day</div></div>
            <div className="card tile"><div className="eyebrow">Contract rules · recall</div><div className="value">{fmt.pct(Math.min(...Object.values(report.data!.rules.recall_by_fault)), 0)}</div><div className="sub">{fmt.int(report.data!.rules.flagged_rows_on_clean_data)} false flags on {fmt.int(report.data!.rules.clean_rows)} clean rows</div></div>
          </div>
        )}
        <p className="small muted">Limits are reported too: volume drops in segments with fewer than ten rows a day are mostly not detectable, and an anomalous value is not proof of an error.</p>
      </section>

      <footer style={{ borderTop: '1px solid var(--line)' }}>
        <div className="row wrap" style={{ maxWidth: 1180, margin: '0 auto', padding: '20px 24px', gap: 16 }}>
          <Logo size={20} /><span className="small muted">Data Sentinel · sample data is synthetic · built with FastAPI, scikit-learn and React</span>
        </div>
      </footer>
    </div>
  )
}
