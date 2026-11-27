import { storedSessionId } from './session'
import type { RunDetail, RunSummary } from '../types'

export class RunNotFoundError extends Error {
  constructor(runId: string) {
    super(`Run ${runId} was not found`)
    this.name = 'RunNotFoundError'
  }
}

function apiHeaders(): Record<string, string> {
  return {
    'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '',
  }
}

/** One run. The server shows it only to the session it belongs to, so a run from
 * another session reads as not found. */
export async function fetchRun(
  runId: string,
  signal?: AbortSignal,
  sessionId: string | null = storedSessionId(),
): Promise<RunDetail> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}${query}`, {
    headers: apiHeaders(),
    signal,
  })
  if (res.status === 404) {
    throw new RunNotFoundError(runId)
  }
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return (await res.json()) as RunDetail
}

export async function fetchRuns(
  options: { sessionId?: string | null; limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<RunSummary[]> {
  // The server lists only one session's runs: the one asked for, else this browser's.
  const sessionId = options.sessionId ?? storedSessionId()
  if (!sessionId) return []
  const params = new URLSearchParams()
  params.set('session_id', sessionId)
  if (options.limit) params.set('limit', String(options.limit))
  if (options.offset) params.set('offset', String(options.offset))
  const query = params.toString()
  const res = await fetch(`/api/runs${query ? `?${query}` : ''}`, {
    headers: apiHeaders(),
    signal: options.signal,
  })
  if (res.status === 403) {
    return [] // a session this server did not issue, e.g. from before a secret change
  }
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  const data = (await res.json()) as { runs?: RunSummary[] }
  return data.runs ?? []
}
