import { useCallback, useEffect, useRef, useState } from 'react'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function message(body: unknown, status: number): string {
  const d = (body as { detail?: unknown })?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d) && d[0]?.msg) return `${d[0].loc?.slice(-1)[0] ?? 'Input'}: ${d[0].msg}`
  return status >= 500 ? 'The server had a problem. Please try again.' : `Request failed (${status}).`
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init
  let res: Response
  try {
    res = await fetch(path, {
      credentials: 'same-origin',
      ...rest,
      headers: json !== undefined ? { 'Content-Type': 'application/json', ...rest.headers } : rest.headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection.')
  }
  const body = res.headers.get('content-type')?.includes('json') ? await res.json() : null
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/')) window.dispatchEvent(new Event('sentinel:signed-out'))
    throw new ApiError(res.status, message(body, res.status))
  }
  return body as T
}

/** GET with loading/error state; `poll` re-fetches every N ms while it returns true. `reload` restarts polling. */
export function useApi<T>(path: string | null, poll?: (data: T) => boolean, every = 2000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!!path)
  const [nonce, setNonce] = useState(0)
  const pollRef = useRef(poll)
  pollRef.current = poll

  useEffect(() => {
    if (!path) return
    let timer: number | undefined
    let alive = true
    setLoading(true)
    const tick = async () => {
      try {
        const d = await api<T>(path)
        if (!alive) return
        setData(d)
        setError(null)
        if (pollRef.current?.(d)) timer = window.setTimeout(tick, every)
      } catch (e) {
        if (alive) setError((e as Error).message)
      } finally {
        if (alive) setLoading(false)
      }
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [path, every, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, loading, reload, setData }
}

// ---------- types
export type Role = 'viewer' | 'analyst' | 'admin'
export interface Me { id: number; name: string; email: string; role: Role; organization: { id: number; name: string; is_demo: boolean } }
export type Severity = 'critical' | 'high' | 'medium' | 'low'
export interface RunSummary {
  rows: number; checks_passed: number; checks_total: number; rule_flagged_rows: number; model_flagged_rows: number
  flagged_periods: number; findings: Record<Severity, number>; baseline: boolean; version: number
}
export interface Run { id: number; version_id: number; status: 'queued' | 'running' | 'succeeded' | 'failed'; stage: string; error: string | null; summary: RunSummary | null; created_at: string; finished_at: string | null }
export interface Version { id: number; number: number; filename: string; source: string; rows: number; columns: number; size_bytes: number; sha256: string; created_at: string; run: Run | null }
export interface Column { name: string; role: string; nunique: number }
export interface Schema { columns: Column[]; time: string | null; ingest_time: string | null; key: string | null; segments: string[]; context: string[]; numeric: string[] }
export interface Rule { type: string; column?: string; value?: number; values?: string[]; expr?: string; name?: string }
export interface ModelInfo { id: number; kind: 'row' | 'period'; detector: string; threshold: number; active: boolean; trained_on_version_id: number; info: { rows: number; features: string[]; context: string[]; segments: string[] | null; days: number | null }; created_at: string }
export interface DatasetDetail { id: number; name: string; description: string; baseline_version_id: number | null; schema: Schema | null; contract: { rules: Rule[] } | null; rules: Rule[]; notes: string[]; versions: Version[]; models: ModelInfo[] }
export interface DatasetRow { id: number; name: string; description: string; versions: number; open_incidents: number; latest_run: Run | null; created_at: string }
export interface Reason { column: string; value: number; expected: number; range: [number, number]; z: number; ratio: number | null; peers: { column: string | null; value: string | null; rows: number; median: number } }
export interface RecordExample { position: number; row_number: number; key: string | null; score: number; rule_violation: boolean; reasons: Reason[]; context: Record<string, string | null> }
export interface FeedReason { metric: string; value: number | null; typical: number | null; basis: string; z: number | null; unit?: string }
export interface FeedDay { segment: string; day: string; score: number; volume_alert: boolean; rows: number; expected_rows: number | null; reasons: FeedReason[] }
export interface Finding { kind: string; fingerprint: string; severity: Severity; title: string; summary: string; evidence: Record<string, unknown> }
export interface RunResults {
  rows: number; columns: number; notes?: string[]; checks: { name: string; passed: boolean }[]; findings: Finding[]; summary: RunSummary
  records?: { scored: number; flagged: number; threshold: number; detector: string; examples: RecordExample[]; score_histogram: { edges: number[]; counts: number[]; threshold: number } }
  feed?: { detector: string; threshold: number; segments: string[]; flagged: FeedDay[]; daily: { day: string; rows: number; expected: number | null }[]; cells: { segment: string; day: string; rows: number; score: number }[] }
}
export interface RunDetail extends Run { results: RunResults | null; version: { id: number; number: number; filename: string; source: string } }
export interface Incident { id: number; dataset_id: number; dataset: string; kind: string; title: string; summary: string; severity: Severity; status: 'open' | 'investigating' | 'resolved' | 'dismissed'; assignee: string | null; assignee_id: number | null; occurrences: number; opened_at: string; last_seen_at: string; resolved_at: string | null; first_run_id: number; last_run_id: number }
export interface IncidentDetail extends Incident { evidence: Record<string, unknown>; events: { id: number; type: string; body: string; created_at: string; user: string | null }[] }
export interface Member { id: number; name: string; email: string; role: Role }
