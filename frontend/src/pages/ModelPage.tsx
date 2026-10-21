import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useApi } from '../api'
import { ErrorBox, fmt, Loading } from '../ui'

interface Metrics { precision: number; recall: number; f1: number; fpr: number; tp: number; fp: number; fn: number; tn: number; average_precision: number; roc_auc: number; per_fault_recall: Record<string, number> }
interface Candidate {
  detector: string; family: 'ml' | 'statistical'; features: string; threshold: number; validation_average_precision: number
  test: Metrics; test_by_stream: Record<string, { mean: number; std: number }>; seconds_per_10k: number; pr_curve: { recall: number; precision: number }[]
}
interface Level {
  candidates: Candidate[]; selected: string; best_statistical: string; test_positives: number; test_items: number
  shipped?: { description: string; test: Omit<Metrics, 'average_precision' | 'roc_auc'>; volume_drop_recall_by_segment_size: { expected_rows: string; volume_drops: number; recall: number | null }[] }
}
export interface Report {
  generated_at: string; runtime_seconds: number
  protocol: { validation_seeds: number[]; test_seeds: number[]; baseline_days: number; scored_days: number }
  levels: { row: Level; period: Level }
  rules: { recall_by_fault: Record<string, number>; precision: number; flagged_rows_on_clean_data: number; clean_rows: number }
}

const FAM = { ml: 'var(--series-1)', statistical: 'var(--series-2)' }

function APChart({ level }: { level: Level }) {
  const data = [...level.candidates].sort((a, b) => b.test.average_precision - a.test.average_precision)
    .map((c) => ({ name: fmt.detector(c.detector), ap: c.test.average_precision, family: c.family, selected: c.detector === level.selected }))
  return (
    <div>
      <div className="legend" style={{ marginBottom: 6 }}>
        <span><i className="dot" style={{ background: FAM.ml, borderRadius: 2 }} /> machine learning</span>
        <span><i className="dot" style={{ background: FAM.statistical, borderRadius: 2 }} /> statistical baseline</span>
      </div>
      <ResponsiveContainer width="100%" height={data.length * 30 + 30}>
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 0 }} barCategoryGap={6}>
          <CartesianGrid horizontal={false} />
          <XAxis type="number" domain={[0, 1]} tickLine={false} tickFormatter={(v) => v.toFixed(1)} />
          <YAxis type="category" dataKey="name" width={270} tickLine={false} axisLine={false} interval={0} />
          <Tooltip cursor={{ fill: 'var(--surface-2)' }} content={({ active, payload }) => active && payload?.length ? (
            <div className="chart-tip"><div className="t">{payload[0].payload.name}</div><div className="r"><span>test average precision</span><b className="num">{(payload[0].value as number).toFixed(3)}</b></div>{payload[0].payload.selected && <div className="small">selected on validation data</div>}</div>) : null} />
          <Bar dataKey="ap" radius={[0, 4, 4, 0]} isAnimationActive={false} label={{ position: 'right', fill: 'var(--ink-2)', fontSize: 11, formatter: (v: unknown) => (v as number).toFixed(2) }}>
            {data.map((d) => <Cell key={d.name} fill={FAM[d.family]} stroke={d.selected ? 'var(--ink)' : 'none'} strokeWidth={2} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function PRChart({ level }: { level: Level }) {
  const pick = [level.selected, level.best_statistical, level.candidates.find((c) => c.detector.startsWith('isolation_forest') && c.detector !== level.selected)?.detector]
    .filter((d, i, a): d is string => !!d && a.indexOf(d) === i)
  const colors = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']
  const series = pick.map((d) => level.candidates.find((c) => c.detector === d)!)
  return (
    <div>
      <div className="legend" style={{ marginBottom: 6 }}>
        {series.map((s, i) => <span key={s.detector}><i className="swatch-line" style={{ background: colors[i] }} /> {fmt.detector(s.detector)} (AP {s.test.average_precision.toFixed(2)})</span>)}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart margin={{ top: 6, right: 12, bottom: 12, left: -8 }}>
          <CartesianGrid />
          <XAxis type="number" dataKey="recall" domain={[0, 1]} tickLine={false} label={{ value: 'recall', position: 'insideBottom', offset: -6, fill: 'var(--muted)', fontSize: 11 }} />
          <YAxis type="number" domain={[0, 1]} tickLine={false} axisLine={false} width={48} />
          <Tooltip content={({ active, payload }) => active && payload?.length ? (
            <div className="chart-tip">{payload.map((p, i) => <div key={i} className="r"><span>{fmt.detector(String(p.name))}</span><span className="num">P {(p.payload.precision as number).toFixed(2)} · R {(p.payload.recall as number).toFixed(2)}</span></div>)}</div>) : null} />
          {series.map((s, i) => <Line key={s.detector} name={s.detector} data={s.pr_curve} dataKey="precision" stroke={colors[i]} strokeWidth={2} dot={false} isAnimationActive={false} type="stepAfter" />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function RecallMatrix({ level }: { level: Level }) {
  const faults = Object.keys(level.candidates[0].test.per_fault_recall)
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Detector</th>{faults.map((f) => <th key={f} className="num">{f.replace(/_/g, ' ')}</th>)}</tr></thead>
      <tbody>{level.candidates.map((c) => (
        <tr key={c.detector} style={c.detector === level.selected ? { fontWeight: 600 } : undefined}>
          <td className="small">{fmt.detector(c.detector)}</td>
          {faults.map((f) => {
            const v = c.test.per_fault_recall[f]
            return <td key={f} className="num small" style={{ background: `color-mix(in srgb, var(--series-1) ${Math.round(v * 50)}%, transparent)` }}>{fmt.pct(v, 0)}</td>
          })}
        </tr>
      ))}</tbody>
    </table></div>
  )
}

function LevelSection({ title, lede, level, unit }: { title: string; lede: string; level: Level; unit: string }) {
  const sel = level.candidates.find((c) => c.detector === level.selected)!
  const stat = level.candidates.find((c) => c.detector === level.best_statistical)!
  return (
    <section className="stack">
      <div><h2 className="display" style={{ fontSize: 30 }}>{title}</h2><p className="ink2" style={{ maxWidth: '75ch' }}>{lede}</p></div>
      <div className="grid g4">
        <div className="card tile"><div className="eyebrow">Selected</div><div className="value" style={{ fontSize: 22 }}>{fmt.detector(sel.detector)}</div><div className="sub">highest validation AP ({sel.validation_average_precision.toFixed(3)})</div></div>
        <div className="card tile"><div className="eyebrow">Test average precision</div><div className="value">{sel.test.average_precision.toFixed(3)}</div><div className="sub">best statistical: {stat.test.average_precision.toFixed(3)} · ±{sel.test_by_stream.average_precision.std.toFixed(3)} across streams</div></div>
        <div className="card tile"><div className="eyebrow">Precision / recall</div><div className="value">{fmt.pct(sel.test.precision, 0)} / {fmt.pct(sel.test.recall, 0)}</div><div className="sub">at threshold {sel.threshold.toFixed(2)} chosen on validation</div></div>
        <div className="card tile"><div className="eyebrow">False-positive rate</div><div className="value">{fmt.pct(sel.test.fpr, 2)}</div><div className="sub">{fmt.int(sel.test.fp)} false alarms in {fmt.int(level.test_items - level.test_positives)} clean {unit}</div></div>
      </div>
      <div className="grid g2">
        <div className="card"><div className="card-head"><h3>Average precision on test data</h3></div><div className="card-body"><APChart level={level} /></div></div>
        <div className="card"><div className="card-head"><h3>Precision–recall curves</h3><span className="small muted">pooled test streams</span></div><div className="card-body"><PRChart level={level} /></div></div>
      </div>
      <div className="card"><div className="card-head"><h3 className="grow">Recall by fault type</h3><span className="small muted">at each detector’s own validation threshold</span></div><RecallMatrix level={level} /></div>
      <div className="card table-wrap"><table>
        <thead><tr><th>Detector</th><th>Family</th><th className="num">Val AP</th><th className="num">Test AP</th><th className="num">ROC AUC</th><th className="num">Precision</th><th className="num">Recall</th><th className="num">F1</th><th className="num">FPR</th><th className="num">s / 10k {unit}</th></tr></thead>
        <tbody>{level.candidates.map((c) => (
          <tr key={c.detector} style={c.detector === level.selected ? { background: 'var(--accent-soft)' } : undefined}>
            <td className="small">{fmt.detector(c.detector)}</td><td className="small">{c.family === 'ml' ? 'ML' : 'statistical'}</td>
            <td className="num">{c.validation_average_precision.toFixed(3)}</td><td className="num">{c.test.average_precision.toFixed(3)}</td><td className="num">{c.test.roc_auc.toFixed(3)}</td>
            <td className="num">{c.test.precision.toFixed(3)}</td><td className="num">{c.test.recall.toFixed(3)}</td><td className="num">{c.test.f1.toFixed(3)}</td><td className="num">{fmt.pct(c.test.fpr, 2)}</td><td className="num">{c.seconds_per_10k.toFixed(1)}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </section>
  )
}

export default function ModelPage() {
  const r = useApi<Report>('/api/model/report')
  if (r.error) return <ErrorBox error={r.error} retry={r.reload} />
  if (!r.data) return <Loading />
  const { levels, protocol, rules } = r.data
  const shipped = levels.period.shipped!
  const selPeriod = levels.period.candidates.find((c) => c.detector === levels.period.selected)!
  return (
    <div className="stack" style={{ gap: 36 }}>
      <div className="page-head"><div className="grow">
        <h1>Model evidence</h1>
        <p className="lede">How the detectors were chosen, on what data, and how they compare with statistical methods. Generated by <code>python -m sentinel.ml_eval</code> on {fmt.date(r.data.generated_at)} ({r.data.runtime_seconds}s).</p>
      </div></div>

      <div className="card pad grid g3">
        <div className="stack-sm"><span className="eyebrow">Data</span><p className="ink2">Independent synthetic retailers (one per seed). Each model learns from a {protocol.baseline_days}-day baseline with a little unlabelled contamination and scores the next {protocol.scored_days} days, into which labelled faults are injected.</p></div>
        <div className="stack-sm"><span className="eyebrow">Selection</span><p className="ink2">Detector chosen by average precision on validation seeds {protocol.validation_seeds.join(', ')}; its threshold maximises validation F1. Nothing is tuned on the test seeds.</p></div>
        <div className="stack-sm"><span className="eyebrow">Test</span><p className="ink2">Reported once on held-out seeds {protocol.test_seeds.join(', ')}. Natural irregularities (bulk orders, clearance discounts, guest checkouts) are left unlabelled, so they count as false positives if flagged.</p></div>
      </div>

      <LevelSection title="Record level" unit="rows" level={levels.row}
        lede="Each row is compared with what its context predicts. Faults: prices ×100 and ÷100 (with revenue kept consistent, so rules cannot see them), quantity spikes, and a contextual error where one SKU’s price is ×10 for three days." />

      <LevelSection title="Feed level" unit="segment-days" level={levels.period}
        lede="Each segment-day is scored on volume vs. same weekday, completeness, duplication, ingestion delay and mix-adjusted price level. Faults: volume drops, late arrival, subtle price drift, missing-value bursts, duplicate bursts." />

      <section className="grid g2">
        <div className="card"><div className="card-head"><h3>Shipped feed configuration</h3></div><div className="card-body stack-sm">
          <p className="ink2">{fmt.detector(levels.period.selected)} <b>or</b> a Poisson volume test (≤ 50% of expected rows and p &lt; 10⁻⁵). On the test streams the test adds {shipped.test.tp - selPeriod.test.tp} detections and {shipped.test.fp - selPeriod.test.fp} false positives; its main job is the baseline version itself, where the learned feed model is not applied in-sample.</p>
          <dl className="reason"><dt>Precision</dt><dd className="num">{shipped.test.precision.toFixed(3)}</dd><dt>Recall</dt><dd className="num">{shipped.test.recall.toFixed(3)}</dd><dt>FPR</dt><dd className="num">{fmt.pct(shipped.test.fpr, 2)}</dd></dl>
          <div className="eyebrow" style={{ marginTop: 8 }}>Volume-drop recall by segment size</div>
          <table><thead><tr><th>Expected rows / day</th><th className="num">Drops</th><th className="num">Recall</th></tr></thead>
            <tbody>{shipped.volume_drop_recall_by_segment_size.map((b) => <tr key={b.expected_rows}><td>{b.expected_rows}</td><td className="num">{b.volume_drops}</td><td className="num">{b.recall == null ? '–' : fmt.pct(b.recall, 0)}</td></tr>)}</tbody></table>
          <p className="small muted">Small segments are the main limitation: losing half of 6 rows is within normal day-to-day noise.</p>
        </div></div>
        <div className="card"><div className="card-head"><h3>Deterministic contract rules</h3></div><div className="card-body stack-sm">
          <p className="ink2">Where an exact rule exists, no model is needed. Rules were evaluated on the same test streams with sign flips, unparseable values, duplicate loads, a price error that breaks <code>revenue = qty × price × (1 − discount)</code>, and missing keys.</p>
          <table><thead><tr><th>Fault</th><th className="num">Recall</th></tr></thead>
            <tbody>{Object.entries(rules.recall_by_fault).map(([k, v]) => <tr key={k}><td>{k.replace(/_/g, ' ')}</td><td className="num">{fmt.pct(v, 0)}</td></tr>)}</tbody></table>
          <p className="small ink2">Precision {fmt.pct(rules.precision, 1)} · {fmt.int(rules.flagged_rows_on_clean_data)} rows flagged on {fmt.int(rules.clean_rows)} clean rows.</p>
        </div></div>
      </section>

      <section className="card pad stack-sm">
        <span className="eyebrow">Reading these numbers honestly</span>
        <ul className="ink2" style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
          <li>All data is synthetic. The faults are realistic, but real feeds have their own quirks; expect to tune thresholds per dataset.</li>
          <li>Isolation Forest was the starting baseline. It underperforms here because features that are constant in the baseline (e.g. duplicate rate = 0) can never be split on, and extreme values in irrelevant dimensions dilute its isolation depth.</li>
          <li>The learning is in the features, not the final scoring rule. A gradient-boosted model per numeric column learns what a value should be for its context; both levels then score deviations from it. Pure statistical baselines see the same rows without that model.</li>
          <li>For the record level, scaling each field by how often the baseline itself deviates that much (calibration) removed most false alarms on zero-inflated columns such as discount.</li>
          <li>An anomalous value is evidence to look at, not proof of an error. Legitimate events (promotions, bulk orders) are deliberately left unlabelled and count against precision.</li>
        </ul>
      </section>
    </div>
  )
}
