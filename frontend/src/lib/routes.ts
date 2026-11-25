export type AppRoute =
  | { view: 'chat' }
  | { view: 'runs'; sessionId: string | null }
  | { view: 'run'; runId: string }

export function parseRoute(pathname: string, search = ''): AppRoute {
  const clean = pathname.replace(/\/+$/, '') || '/'
  if (clean === '/runs') {
    const params = new URLSearchParams(search)
    const sessionId = params.get('session_id')?.trim() || null
    return { view: 'runs', sessionId }
  }
  const match = /^\/runs\/([^/]+)$/.exec(clean)
  if (match) {
    try {
      return { view: 'run', runId: decodeURIComponent(match[1]) }
    } catch {
      return { view: 'run', runId: match[1] }
    }
  }
  return { view: 'chat' }
}

export function runsPath(sessionId?: string | null): string {
  if (sessionId) {
    return `/runs?session_id=${encodeURIComponent(sessionId)}`
  }
  return '/runs'
}

export function runPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}`
}
