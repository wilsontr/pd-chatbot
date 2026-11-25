import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Coins,
  Copy,
  Cpu,
  ExternalLink,
  FileCode2,
  Flag,
  Layers,
  Loader2,
  MessageSquare,
  Pause,
  Play,
  RotateCcw,
  Search,
  SkipForward,
  Volume2,
  Wrench,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { PatchAudioPlayer } from './PatchAudioPlayer'
import { PdPatchViewer } from './PdPatchViewer'
import { fetchRun, RunNotFoundError } from '@/lib/runsApi'
import { runsPath } from '@/lib/routes'
import {
  buildTimelineSteps,
  cumulativeMetrics,
  formatCost,
  formatLatency,
  formatTimestamp,
  formatTokens,
  type TimelineStep,
} from '@/lib/runTimeline'
import type { RunDetail } from '../types'

const PLAYBACK_INTERVAL_MS = 650

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'completed'
      ? 'border-emerald-300 bg-emerald-500/10 text-emerald-700 dark:border-emerald-800 dark:text-emerald-400'
      : status === 'failed'
      ? 'border-destructive/40 bg-destructive/10 text-destructive'
      : status === 'refused'
      ? 'border-amber-300 bg-amber-500/10 text-amber-800 dark:border-amber-800 dark:text-amber-300'
      : 'border-blue-300 bg-blue-500/10 text-blue-700 dark:border-blue-800 dark:text-blue-400'
  return (
    <Badge variant="outline" className={`text-[11px] font-medium capitalize ${cls}`}>
      {status}
    </Badge>
  )
}

function StepIcon({ step }: { step: TimelineStep }) {
  const cls = 'size-4 shrink-0'
  switch (step.kind) {
    case 'model_turn':
      return <Bot className={`${cls} text-indigo-600 dark:text-indigo-400`} />
    case 'tool_call':
      if (step.toolName === 'search_docs') return <Search className={`${cls} text-amber-600 dark:text-amber-400`} />
      if (step.toolName === 'write_patch') return <FileCode2 className={`${cls} text-amber-600 dark:text-amber-400`} />
      if (step.toolName === 'run_patch') return <Play className={`${cls} text-amber-600 dark:text-amber-400`} />
      if (step.toolName === 'analyze_audio') return <Activity className={`${cls} text-amber-600 dark:text-amber-400`} />
      return <Wrench className={`${cls} text-amber-600 dark:text-amber-400`} />
    case 'tool_result':
      if (step.status === 'error') return <AlertCircle className={`${cls} text-destructive`} />
      if (step.status === 'warning') return <AlertTriangle className={`${cls} text-amber-600 dark:text-amber-400`} />
      return <Check className={`${cls} text-emerald-600 dark:text-emerald-400`} />
    case 'sources':
      return <BookOpen className={`${cls} text-sky-600 dark:text-sky-400`} />
    case 'text_output':
      return <MessageSquare className={`${cls} text-muted-foreground`} />
    case 'text_reset':
      return <RotateCcw className={`${cls} text-amber-600 dark:text-amber-400`} />
    case 'artifact':
      return step.artifactKind === 'audio' ? (
        <Volume2 className={`${cls} text-purple-600 dark:text-purple-400`} />
      ) : (
        <Layers className={`${cls} text-purple-600 dark:text-purple-400`} />
      )
    case 'done':
      return <Flag className={`${cls} text-emerald-600 dark:text-emerald-400`} />
  }
}

function kindPillLabel(step: TimelineStep): string {
  switch (step.kind) {
    case 'model_turn':
      return 'Model turn'
    case 'tool_call':
      return 'Tool call'
    case 'tool_result':
      return 'Tool result'
    case 'sources':
      return 'Sources'
    case 'text_output':
      return 'Answer'
    case 'text_reset':
      return 'Text reset'
    case 'artifact':
      return 'Artifact'
    case 'done':
      return 'Done'
  }
}

function StepBody({ step }: { step: TimelineStep }) {
  if (step.kind === 'model_turn') {
    const breakdown = step.tokenBreakdown ?? {}
    return (
      <div className="space-y-3 text-xs">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground font-mono text-[11px]">
          <span>input: {(breakdown.input ?? 0).toLocaleString()}</span>
          <span>output: {(breakdown.output ?? 0).toLocaleString()}</span>
          {(breakdown.cache_read ?? 0) > 0 && (
            <span>cache_read: {(breakdown.cache_read ?? 0).toLocaleString()}</span>
          )}
          {(breakdown.cache_write ?? 0) > 0 && (
            <span>cache_write: {(breakdown.cache_write ?? 0).toLocaleString()}</span>
          )}
          {step.stopReason && <span>stop: {step.stopReason}</span>}
        </div>
        {step.text && (
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-1">Assistant text</div>
            <div className="rounded-lg bg-muted/50 border border-border/70 px-3 py-2 text-xs whitespace-pre-wrap">
              {step.text}
            </div>
          </div>
        )}
        {step.contentBlocks && step.contentBlocks.length > 0 && (
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-1">
              Content blocks ({step.contentBlocks.length})
            </div>
            <pre className="rounded-lg bg-muted/50 border border-border/70 px-3 py-2 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
              {JSON.stringify(step.contentBlocks, null, 2)}
            </pre>
          </div>
        )}
      </div>
    )
  }

  if (step.kind === 'tool_call') {
    return (
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>tool: {step.toolName}</span>
          {step.toolId && <span>id: {step.toolId}</span>}
        </div>
        <pre className="rounded-lg bg-muted/50 border border-border/70 px-3 py-2 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
          {JSON.stringify(step.toolArguments ?? {}, null, 2)}
        </pre>
      </div>
    )
  }

  if (step.kind === 'tool_result') {
    const hasData = !!step.toolData && Object.keys(step.toolData).length > 0
    return (
      <div className="space-y-3 text-xs">
        {step.toolSummary && (
          <div className={step.status === 'error' ? 'text-destructive' : 'text-foreground/90'}>
            <span className="font-medium text-muted-foreground">Summary: </span>
            {step.toolSummary}
          </div>
        )}
        {hasData && (
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-1">Structured data</div>
            <pre className="rounded-lg bg-muted/50 border border-border/70 px-3 py-2 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-56 overflow-y-auto">
              {JSON.stringify(step.toolData, null, 2)}
            </pre>
          </div>
        )}
        {step.toolChunks && step.toolChunks.length > 0 && (
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-1">
              Retrieved chunks ({step.toolChunks.length})
            </div>
            <ul className="list-disc pl-4 space-y-0.5 text-[11px] text-muted-foreground">
              {step.toolChunks.map((chunk, idx) => (
                <li key={idx}>
                  {typeof chunk.heading_path === 'string'
                    ? chunk.heading_path
                    : typeof chunk.id === 'string'
                    ? chunk.id
                    : `Chunk #${idx + 1}`}
                </li>
              ))}
            </ul>
          </div>
        )}
        {step.toolContent && (
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-1">
              Tool output sent to model
            </div>
            <pre className="rounded-lg bg-muted/50 border border-border/70 px-3 py-2 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
              {step.toolContent}
            </pre>
          </div>
        )}
      </div>
    )
  }

  if (step.kind === 'sources') {
    if (!step.sources || step.sources.length === 0) {
      return <p className="text-xs text-muted-foreground">No citations retrieved.</p>
    }
    return (
      <div className="flex flex-wrap gap-1.5">
        {step.sources.map((src, i) => (
          <a key={i} href={src.url} target="_blank" rel="noreferrer">
            <Badge
              variant="outline"
              className="text-xs font-normal hover:bg-accent transition-colors"
            >
              {src.object_name ?? src.heading_path}
            </Badge>
          </a>
        ))}
      </div>
    )
  }

  if (step.kind === 'text_output') {
    return (
      <div className="rounded-lg bg-muted/40 border border-border/70 px-3.5 py-2.5 text-sm leading-relaxed prose-bubble">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{step.text ?? ''}</ReactMarkdown>
      </div>
    )
  }

  if (step.kind === 'text_reset') {
    return (
      <p className="text-xs text-muted-foreground">
        The model wrote preliminary text and then invoked a tool; the stream reset so only the
        verified final answer is shown to the user.
      </p>
    )
  }

  if (step.kind === 'artifact') {
    if (step.patchArtifact?.patch) {
      return (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground font-mono">
            {step.patchArtifact.name}
          </div>
          <PdPatchViewer patch={step.patchArtifact.patch} />
        </div>
      )
    }
    if (step.audioArtifact) {
      return <PatchAudioPlayer audio={step.audioArtifact} />
    }
    return (
      <pre className="rounded-lg bg-muted/50 border border-border/70 px-3 py-2 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap">
        {JSON.stringify(step.artifactData ?? {}, null, 2)}
      </pre>
    )
  }

  if (step.kind === 'done') {
    const meta = step.doneMeta
    return (
      <div className="space-y-2.5 text-xs">
        {meta && (
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline" className="text-[11px] font-mono">
              {meta.turns} {meta.turns === 1 ? 'turn' : 'turns'}
            </Badge>
            <Badge variant="outline" className="text-[11px] font-mono">
              {meta.toolCalls} {meta.toolCalls === 1 ? 'tool call' : 'tool calls'}
            </Badge>
            {meta.cached && <Badge variant="secondary">cached</Badge>}
            {meta.refused && <Badge variant="destructive">refused</Badge>}
            {meta.budgetExceeded && (
              <Badge variant="destructive">budget exceeded: {meta.budgetExceeded}</Badge>
            )}
            {meta.patchFailed && <Badge variant="destructive">patch invalid</Badge>}
          </div>
        )}
        {step.text && (
          <div className="rounded-lg bg-muted/40 border border-border/70 px-3.5 py-2.5 text-sm leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{step.text}</ReactMarkdown>
          </div>
        )}
      </div>
    )
  }

  return null
}

function defaultExpanded(step: TimelineStep): boolean {
  return step.kind === 'artifact' || step.kind === 'sources'
}

export function RunTimelinePage({
  runId,
  onNavigate,
}: {
  runId: string
  onNavigate: (path: string) => void
}) {
  const [run, setRun] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [copied, setCopied] = useState(false)

  const [visibleCount, setVisibleCount] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [expandedMap, setExpandedMap] = useState<Record<string, boolean>>({})

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setLoading(true)
    setNotFound(false)
    setError(null)
    setPlaying(false)

    fetchRun(runId, controller.signal)
      .then(data => {
        if (cancelled) return
        setRun(data)
        const built = buildTimelineSteps(data)
        setVisibleCount(built.length)
        const initialExpanded: Record<string, boolean> = {}
        for (const s of built) {
          if (defaultExpanded(s)) initialExpanded[s.id] = true
        }
        setExpandedMap(initialExpanded)
        setLoading(false)
      })
      .catch(err => {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return
        if (err instanceof RunNotFoundError) {
          setNotFound(true)
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load run')
        }
        setLoading(false)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [runId, attempt])

  const steps = useMemo(() => (run ? buildTimelineSteps(run) : []), [run])
  const visibleSteps = useMemo(() => steps.slice(0, visibleCount), [steps, visibleCount])
  const cumulative = useMemo(
    () => cumulativeMetrics(steps, visibleCount),
    [steps, visibleCount],
  )

  useEffect(() => {
    if (!playing) return
    if (visibleCount >= steps.length) {
      setPlaying(false)
      return
    }
    const timer = window.setTimeout(() => {
      setVisibleCount(prev => Math.min(prev + 1, steps.length))
    }, PLAYBACK_INTERVAL_MS)
    return () => window.clearTimeout(timer)
  }, [playing, visibleCount, steps.length])

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (steps.length === 0) return
      if (e.key === 'ArrowRight') {
        e.preventDefault()
        setPlaying(false)
        setVisibleCount(prev => Math.min(prev + 1, steps.length))
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        setPlaying(false)
        setVisibleCount(prev => Math.max(prev - 1, 1))
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [steps.length])

  function handleReplayFromStart() {
    if (steps.length === 0) return
    setVisibleCount(1)
    setPlaying(steps.length > 1)
  }

  function handleTogglePlay() {
    if (steps.length === 0) return
    if (playing) {
      setPlaying(false)
      return
    }
    if (visibleCount >= steps.length) {
      setVisibleCount(1)
    }
    setPlaying(true)
  }

  function handleCopyId() {
    navigator.clipboard?.writeText(runId).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    })
  }

  function setAllExpanded(expanded: boolean) {
    const next: Record<string, boolean> = {}
    for (const s of steps) next[s.id] = expanded
    setExpandedMap(next)
  }

  const allExpanded = steps.length > 0 && visibleSteps.every(s => !!expandedMap[s.id])

  return (
    <ScrollArea className="flex-1 min-h-0">
      <div className="max-w-[920px] mx-auto px-4 py-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onNavigate(runsPath())}
            className="gap-1.5 -ml-2 text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            All runs
          </Button>
          {run?.langfuse_trace_url && (
            <a
              href={run.langfuse_trace_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground border border-border rounded-md px-2.5 py-1 transition-colors"
            >
              View Langfuse trace
              <ExternalLink className="size-3" />
            </a>
          )}
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-12 justify-center">
            <Loader2 className="size-4 animate-spin" />
            Loading run <code className="font-mono text-xs">{runId}</code>…
          </div>
        )}

        {!loading && notFound && (
          <div className="rounded-xl border border-border bg-muted/30 p-6 text-center space-y-3">
            <p className="text-sm font-medium">Run not found</p>
            <p className="text-xs text-muted-foreground">
              No stored run matches <code className="font-mono">{runId}</code>.
            </p>
            <Button variant="outline" size="sm" onClick={() => onNavigate(runsPath())}>
              Browse stored runs
            </Button>
          </div>
        )}

        {!loading && error && (
          <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-6 text-center space-y-3">
            <p className="text-sm font-medium text-destructive">Could not load run</p>
            <p className="text-xs text-muted-foreground">{error}</p>
            <Button variant="outline" size="sm" onClick={() => setAttempt(n => n + 1)}>
              Retry
            </Button>
          </div>
        )}

        {!loading && run && (
          <>
            {/* Run summary header */}
            <section className="rounded-xl border border-border bg-card p-5 space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1 min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={run.status} />
                    {run.model && (
                      <Badge variant="outline" className="font-mono text-[11px] font-normal">
                        {run.model}
                      </Badge>
                    )}
                    {run.query_type && (
                      <Badge variant="outline" className="text-[11px] font-normal text-muted-foreground">
                        {run.query_type}
                      </Badge>
                    )}
                    {run.cached && (
                      <Badge variant="secondary" className="text-[11px]">
                        cached
                      </Badge>
                    )}
                  </div>
                  <h2 className="text-base font-semibold leading-snug break-words">
                    {run.question}
                  </h2>
                </div>
                <div className="text-right text-[11px] text-muted-foreground shrink-0">
                  <div>{formatTimestamp(run.created_at)}</div>
                  <div className="capitalize">{run.interface} interface</div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <span className="font-medium text-foreground/80">Run:</span>
                  <code className="font-mono text-[11px]">{run.run_id}</code>
                  <button
                    type="button"
                    onClick={handleCopyId}
                    className="p-0.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground cursor-pointer"
                    title="Copy run ID"
                  >
                    {copied ? <Check className="size-3 text-emerald-600" /> : <Copy className="size-3" />}
                  </button>
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="font-medium text-foreground/80">Session:</span>
                  <button
                    type="button"
                    onClick={() => onNavigate(runsPath(run.session_id))}
                    className="font-mono text-[11px] underline underline-offset-2 hover:text-foreground cursor-pointer"
                  >
                    {run.session_id}
                  </button>
                </span>
                {run.langfuse_trace_id && !run.langfuse_trace_url && (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="font-medium text-foreground/80">Trace:</span>
                    <code className="font-mono text-[11px]">{run.langfuse_trace_id}</code>
                  </span>
                )}
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 pt-2 border-t border-border/70">
                <div className="rounded-lg bg-muted/40 px-3 py-2">
                  <div className="text-[11px] text-muted-foreground">Model turns</div>
                  <div className="text-sm font-semibold font-mono">{run.turns}</div>
                </div>
                <div className="rounded-lg bg-muted/40 px-3 py-2">
                  <div className="text-[11px] text-muted-foreground">Tool calls</div>
                  <div className="text-sm font-semibold font-mono">{run.tool_calls}</div>
                </div>
                <div className="rounded-lg bg-muted/40 px-3 py-2">
                  <div className="text-[11px] text-muted-foreground">Total tokens</div>
                  <div className="text-sm font-semibold font-mono">
                    {formatTokens(run.total_tokens)}
                  </div>
                </div>
                <div className="rounded-lg bg-muted/40 px-3 py-2">
                  <div className="text-[11px] text-muted-foreground">Total cost</div>
                  <div className="text-sm font-semibold font-mono">
                    {formatCost(run.cost_usd)}
                  </div>
                </div>
                <div className="rounded-lg bg-muted/40 px-3 py-2">
                  <div className="text-[11px] text-muted-foreground">Total latency</div>
                  <div className="text-sm font-semibold font-mono">
                    {formatLatency(run.latency_ms)}
                  </div>
                </div>
              </div>
            </section>

            {/* Step-by-step replay controls */}
            {steps.length > 0 && (
              <section
                aria-label="Replay controls"
                className="rounded-xl border border-border bg-muted/30 px-4 py-3 space-y-2.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleReplayFromStart}
                      className="gap-1.5 h-7 text-xs"
                    >
                      <RotateCcw className="size-3" />
                      Replay
                    </Button>
                    <Button
                      variant={playing ? 'default' : 'outline'}
                      size="sm"
                      onClick={handleTogglePlay}
                      className="gap-1.5 h-7 text-xs"
                    >
                      {playing ? <Pause className="size-3" /> : <Play className="size-3" />}
                      {playing ? 'Pause' : 'Play'}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setPlaying(false)
                        setVisibleCount(prev => Math.max(prev - 1, 1))
                      }}
                      disabled={visibleCount <= 1}
                      className="h-7 px-2 text-xs"
                      aria-label="Previous step"
                    >
                      <ChevronLeft className="size-3.5" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setPlaying(false)
                        setVisibleCount(prev => Math.min(prev + 1, steps.length))
                      }}
                      disabled={visibleCount >= steps.length}
                      className="h-7 px-2 text-xs"
                      aria-label="Next step"
                    >
                      <ChevronRight className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setPlaying(false)
                        setVisibleCount(steps.length)
                      }}
                      disabled={visibleCount >= steps.length}
                      className="gap-1 h-7 text-xs text-muted-foreground"
                    >
                      <SkipForward className="size-3" />
                      Show all
                    </Button>
                  </div>

                  <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono">
                    <span>
                      Step {visibleCount} / {steps.length}
                    </span>
                    <button
                      type="button"
                      onClick={() => setAllExpanded(!allExpanded)}
                      className="font-sans text-xs underline underline-offset-2 hover:text-foreground cursor-pointer"
                    >
                      {allExpanded ? 'Collapse all' : 'Expand all'}
                    </button>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <input
                    type="range"
                    min={1}
                    max={steps.length}
                    value={Math.max(1, visibleCount)}
                    onChange={e => {
                      setPlaying(false)
                      setVisibleCount(Number(e.target.value))
                    }}
                    aria-label="Replay step scrubber"
                    className="flex-1 min-w-[160px] accent-primary cursor-pointer"
                  />
                  <div className="flex flex-wrap items-center gap-3 text-[11px] font-mono text-muted-foreground">
                    <span>Cumulative: {formatTokens(cumulative.tokens)}</span>
                    <span>{formatCost(cumulative.costUsd)}</span>
                    <span>{formatLatency(cumulative.latencyMs)}</span>
                  </div>
                </div>
              </section>
            )}

            {/* Ordered timeline steps */}
            {steps.length === 0 ? (
              <div className="rounded-xl border border-border bg-muted/20 p-6 text-center text-xs text-muted-foreground">
                No events were recorded for this run.
              </div>
            ) : (
              <ol className="space-y-2.5" aria-label="Run event timeline">
                {visibleSteps.map((step, idx) => {
                  const isActive = idx === visibleSteps.length - 1 && visibleCount < steps.length
                  const isExpanded = !!expandedMap[step.id]

                  return (
                    <li
                      key={step.id}
                      className={`rounded-xl border bg-card transition-colors ${
                        isActive
                          ? 'border-primary/60 ring-1 ring-primary/20'
                          : 'border-border'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedMap(prev => ({ ...prev, [step.id]: !prev[step.id] }))
                        }
                        aria-expanded={isExpanded}
                        className="w-full px-4 py-3 text-left flex flex-col gap-2 hover:bg-muted/30 transition-colors rounded-xl cursor-pointer"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-center gap-2 min-w-0">
                            {isExpanded ? (
                              <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />
                            ) : (
                              <ChevronRight className="size-3.5 text-muted-foreground shrink-0" />
                            )}
                            <span className="font-mono text-[11px] text-muted-foreground shrink-0 w-6">
                              #{step.index + 1}
                            </span>
                            <StepIcon step={step} />
                            <Badge variant="secondary" className="text-[10px] font-medium shrink-0">
                              {kindPillLabel(step)}
                            </Badge>
                            <span className="text-sm font-medium truncate">{step.title}</span>
                            {step.subtitle && (
                              <span className="text-xs text-muted-foreground truncate hidden sm:inline">
                                · {step.subtitle}
                              </span>
                            )}
                          </div>
                          {step.offsetMs !== null && (
                            <span className="font-mono text-[11px] text-muted-foreground shrink-0">
                              +{formatLatency(step.offsetMs)}
                            </span>
                          )}
                        </div>

                        {step.subtitle && (
                          <div className="text-xs text-muted-foreground truncate sm:hidden pl-12">
                            {step.subtitle}
                          </div>
                        )}

                        {/* Per-step tokens, cost and latency */}
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pl-12 text-[11px] font-mono text-muted-foreground">
                          <span className="inline-flex items-center gap-1" title="Tokens">
                            <Cpu className="size-3 opacity-70" />
                            <span>{formatTokens(step.tokens)}</span>
                          </span>
                          <span className="inline-flex items-center gap-1" title="Cost">
                            <Coins className="size-3 opacity-70" />
                            <span>{formatCost(step.costUsd)}</span>
                          </span>
                          <span className="inline-flex items-center gap-1" title="Latency">
                            <Clock className="size-3 opacity-70" />
                            <span>{formatLatency(step.latencyMs)}</span>
                          </span>
                          <span className="text-muted-foreground/60 ml-auto">
                            seq {step.seqStart === step.seqEnd ? step.seqStart : `${step.seqStart}–${step.seqEnd}`}
                          </span>
                        </div>
                      </button>

                      {isExpanded && (
                        <div className="px-4 pb-4 pt-2 border-t border-border/60">
                          <StepBody step={step} />
                        </div>
                      )}
                    </li>
                  )
                })}
              </ol>
            )}
          </>
        )}
      </div>
    </ScrollArea>
  )
}
