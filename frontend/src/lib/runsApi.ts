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

export async function fetchRun(runId: string, signal?: AbortSignal): Promise<RunDetail> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
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
  const params = new URLSearchParams()
  if (options.sessionId) params.set('session_id', options.sessionId)
  if (options.limit) params.set('limit', String(options.limit))
  if (options.offset) params.set('offset', String(options.offset))
  const query = params.toString()
  const res = await fetch(`/api/runs${query ? `?${query}` : ''}`, {
    headers: apiHeaders(),
    signal: options.signal,
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  const data = (await res.json()) as { runs?: RunSummary[] }
  return data.runs ?? []
}
