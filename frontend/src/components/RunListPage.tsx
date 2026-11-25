import { FormEvent, useEffect, useState } from 'react'
import { ArrowRight, Clock, Coins, Cpu, History, Loader2, RefreshCw, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { fetchRuns } from '@/lib/runsApi'
import { runPath, runsPath } from '@/lib/routes'
import {
  formatCost,
  formatLatency,
  formatTimestamp,
  formatTokens,
} from '@/lib/runTimeline'
import type { RunSummary } from '../types'

export function RunListPage({
  sessionId,
  onNavigate,
}: {
  sessionId: string | null
  onNavigate: (path: string) => void
}) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lookupId, setLookupId] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setLoading(true)
    setError(null)

    fetchRuns({ sessionId, limit: 50, signal: controller.signal })
      .then(items => {
        if (cancelled) return
        setRuns(items)
        setLoading(false)
      })
      .catch(err => {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return
        setError(err instanceof Error ? err.message : 'Could not load runs')
        setLoading(false)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [sessionId, attempt])

  function handleLookup(e: FormEvent) {
    e.preventDefault()
    const trimmed = lookupId.trim()
    if (!trimmed) return
    onNavigate(runPath(trimmed))
  }

  return (
    <ScrollArea className="flex-1 min-h-0">
      <div className="max-w-[920px] mx-auto px-4 py-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold flex items-center gap-2">
              <History className="size-4 text-muted-foreground" />
              Run Timeline Replay
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Inspect stored agent runs step by step — model turns, tool calls, results, artifacts,
              tokens, cost, and latency.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAttempt(n => n + 1)}
            disabled={loading}
            className="gap-1.5 h-8 text-xs"
          >
            <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>

        {/* Direct run ID jump */}
        <form onSubmit={handleLookup} className="flex gap-2">
          <input
            type="text"
            value={lookupId}
            onChange={e => setLookupId(e.target.value)}
            placeholder="Jump to run ID…"
            aria-label="Run ID"
            className="flex-1 rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <Button type="submit" size="sm" disabled={!lookupId.trim()} className="gap-1.5">
            Open run
            <ArrowRight className="size-3.5" />
          </Button>
        </form>

        {sessionId && (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Filtered by session:</span>
            <Badge variant="secondary" className="font-mono text-[11px] gap-1.5">
              {sessionId}
              <button
                type="button"
                onClick={() => onNavigate(runsPath())}
                className="hover:text-foreground cursor-pointer"
                aria-label="Clear session filter"
              >
                <X className="size-3" />
              </button>
            </Badge>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading stored runs…
          </div>
        )}

        {!loading && error && (
          <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-6 text-center space-y-2">
            <p className="text-sm font-medium text-destructive">Failed to load runs</p>
            <p className="text-xs text-muted-foreground">{error}</p>
            <Button variant="outline" size="sm" onClick={() => setAttempt(n => n + 1)}>
              Retry
            </Button>
          </div>
        )}

        {!loading && !error && runs.length === 0 && (
          <div className="rounded-xl border border-border bg-muted/20 p-8 text-center space-y-2">
            <p className="text-sm font-medium">No runs recorded yet</p>
            <p className="text-xs text-muted-foreground">
              Ask a question in the chat tab and it will appear here for step-by-step replay.
            </p>
            <Button variant="outline" size="sm" onClick={() => onNavigate('/')}>
              Go to Chat
            </Button>
          </div>
        )}

        {!loading && !error && runs.length > 0 && (
          <ul className="space-y-2">
            {runs.map(run => (
              <li key={run.run_id}>
                <button
                  type="button"
                  onClick={() => onNavigate(runPath(run.run_id))}
                  className="w-full text-left rounded-xl border border-border bg-card hover:bg-muted/30 px-4 py-3 transition-colors space-y-2 cursor-pointer"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge
                          variant="outline"
                          className={`text-[10px] capitalize ${
                            run.status === 'completed'
                              ? 'border-emerald-300 text-emerald-700 dark:border-emerald-800 dark:text-emerald-400'
                              : run.status === 'failed'
                              ? 'border-destructive/40 text-destructive'
                              : 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-300'
                          }`}
                        >
                          {run.status}
                        </Badge>
                        <code className="font-mono text-[11px] text-muted-foreground">
                          {run.run_id.slice(0, 12)}
                        </code>
                        {run.cached && (
                          <Badge variant="secondary" className="text-[10px]">
                            cached
                          </Badge>
                        )}
                      </div>
                      <p className="text-sm font-medium truncate">{run.question}</p>
                    </div>
                    <span className="text-[11px] text-muted-foreground shrink-0">
                      {formatTimestamp(run.created_at)}
                    </span>
                  </div>

                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-mono text-muted-foreground">
                    <span>
                      {run.turns} {run.turns === 1 ? 'turn' : 'turns'} · {run.tool_calls}{' '}
                      {run.tool_calls === 1 ? 'tool' : 'tools'}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Cpu className="size-3 opacity-70" />
                      {formatTokens(run.total_tokens)}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Coins className="size-3 opacity-70" />
                      {formatCost(run.cost_usd)}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Clock className="size-3 opacity-70" />
                      {formatLatency(run.latency_ms)}
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </ScrollArea>
  )
}
