import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  FileCode2,
  Loader2,
  Play,
  Search,
  Wrench,
} from 'lucide-react'
import type { AgentStep } from '../types'

function stringArg(args: Record<string, unknown>, key: string): string | null {
  const val = args[key]
  return typeof val === 'string' && val.trim() ? val.trim() : null
}

function numberArg(args: Record<string, unknown>, key: string): number | null {
  const val = args[key]
  return typeof val === 'number' && Number.isFinite(val) ? val : null
}

function isNonCleanRun(step: AgentStep): boolean {
  return (
    step.status === 'done' &&
    step.name === 'run_patch' &&
    step.data?.ran_cleanly === false
  )
}

function ToolIcon({ name }: { name: string }) {
  const cls = 'size-3.5 text-muted-foreground shrink-0'
  switch (name) {
    case 'search_docs':
      return <Search className={cls} />
    case 'write_patch':
      return <FileCode2 className={cls} />
    case 'run_patch':
      return <Play className={cls} />
    case 'analyze_audio':
      return <Activity className={cls} />
    default:
      return <Wrench className={cls} />
  }
}

function StepStatusIcon({ step }: { step: AgentStep }) {
  if (step.status === 'running') {
    return <Loader2 className="size-3.5 animate-spin text-muted-foreground shrink-0" aria-label="Running" />
  }
  if (step.status === 'error') {
    return <AlertCircle className="size-3.5 text-destructive shrink-0" aria-label="Failed" />
  }
  if (isNonCleanRun(step)) {
    return <AlertTriangle className="size-3.5 text-amber-600 dark:text-amber-400 shrink-0" aria-label="Pd errors" />
  }
  return <Check className="size-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" aria-label="Done" />
}

function stepLabel(step: AgentStep): { action: string; target?: string } {
  const args = step.arguments
  switch (step.name) {
    case 'search_docs': {
      const q = stringArg(args, 'query')
      return { action: 'Search docs', target: q ? `"${q}"` : undefined }
    }
    case 'write_patch': {
      const name = stringArg(args, 'name')
      return { action: 'Write patch', target: name ?? undefined }
    }
    case 'run_patch': {
      const name = stringArg(args, 'name')
      const secs = numberArg(args, 'seconds')
      const target = name ? (secs ? `${name} (${secs}s)` : name) : undefined
      return { action: 'Run patch', target }
    }
    case 'analyze_audio': {
      const name = stringArg(args, 'name')
      return { action: 'Analyze audio', target: name ?? undefined }
    }
    default:
      return { action: step.name }
  }
}

function activeVerb(step: AgentStep): string {
  const args = step.arguments
  switch (step.name) {
    case 'search_docs':
      return 'Searching docs…'
    case 'write_patch': {
      const name = stringArg(args, 'name')
      return name ? `Writing ${name}…` : 'Writing patch…'
    }
    case 'run_patch': {
      const name = stringArg(args, 'name')
      return name ? `Running ${name}…` : 'Running patch…'
    }
    case 'analyze_audio': {
      const name = stringArg(args, 'name')
      return name ? `Analyzing ${name}…` : 'Analyzing audio…'
    }
    default:
      return `Running ${step.name}…`
  }
}

function completedSummary(steps: AgentStep[]): string {
  const verbs: string[] = []
  const add = (label: string) => {
    if (!verbs.includes(label)) verbs.push(label)
  }
  for (const step of steps) {
    if (step.name === 'search_docs') add('searched docs')
    else if (step.name === 'write_patch') add('wrote patch')
    else if (step.name === 'run_patch') add('ran patch')
    else if (step.name === 'analyze_audio') add('analyzed audio')
    else add(step.name)
  }
  return verbs.join(', ')
}

function StepDetails({ step }: { step: AgentStep }) {
  const args = step.arguments
  const data = step.data

  if (step.name === 'search_docs') {
    const query = stringArg(args, 'query')
    const contentType = stringArg(args, 'content_type')
    const objectNames = Array.isArray(args.object_names)
      ? args.object_names.filter((v): v is string => typeof v === 'string')
      : []
    const headings = Array.isArray(data?.headings)
      ? data.headings.filter((v): v is string => typeof v === 'string' && v.length > 0)
      : []

    return (
      <div className="space-y-1.5 text-xs text-muted-foreground">
        {query && (
          <div>
            <span className="font-medium text-foreground/80">Query:</span>{' '}
            <code className="font-mono text-[11px]">{query}</code>
          </div>
        )}
        {(contentType || objectNames.length > 0) && (
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {contentType && (
              <span>
                <span className="font-medium text-foreground/80">Corpus:</span> {contentType}
              </span>
            )}
            {objectNames.length > 0 && (
              <span>
                <span className="font-medium text-foreground/80">Objects:</span>{' '}
                {objectNames.map(n => `[${n}]`).join(', ')}
              </span>
            )}
          </div>
        )}
        {step.summary && (
          <div>
            <span className="font-medium text-foreground/80">Result:</span> {step.summary}
          </div>
        )}
        {headings.length > 0 && (
          <ul className="list-disc pl-4 space-y-0.5 text-[11px]">
            {headings.map((heading, idx) => (
              <li key={idx}>{heading}</li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  if (step.name === 'write_patch') {
    const objects = Array.isArray(args.objects)
      ? args.objects.filter(
          (item): item is { text?: unknown; type?: unknown } =>
            typeof item === 'object' && item !== null,
        )
      : []
    const connections = Array.isArray(args.connections) ? args.connections : []
    const revision = typeof data?.revision === 'number' ? data.revision : null

    return (
      <div className="space-y-1.5 text-xs text-muted-foreground">
        <div>
          <span className="font-medium text-foreground/80">Patch:</span>{' '}
          {objects.length} {objects.length === 1 ? 'box' : 'boxes'}, {connections.length}{' '}
          {connections.length === 1 ? 'connection' : 'connections'}
          {revision !== null ? ` (rev ${revision})` : ''}
        </div>
        {objects.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {objects.map((obj, idx) => (
              <code
                key={idx}
                className="rounded bg-background/80 border border-border/70 px-1.5 py-0.5 font-mono text-[11px] text-foreground/85"
              >
                {typeof obj.text === 'string' && obj.text ? obj.text : String(obj.type ?? 'box')}
              </code>
            ))}
          </div>
        )}
        {step.summary && (
          <div className={step.status === 'error' ? 'text-destructive' : undefined}>
            <span className="font-medium text-foreground/80">Result:</span> {step.summary}
          </div>
        )}
      </div>
    )
  }

  if (step.name === 'run_patch') {
    const seconds = numberArg(args, 'seconds')
    const inputs = Array.isArray(args.inputs)
      ? args.inputs.filter(
          (item): item is { id?: unknown; value?: unknown; at_ms?: unknown } =>
            typeof item === 'object' && item !== null,
        )
      : []
    const errors = Array.isArray(data?.errors)
      ? data.errors.filter((v): v is string => typeof v === 'string')
      : []

    return (
      <div className="space-y-1.5 text-xs text-muted-foreground">
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {seconds !== null && (
            <span>
              <span className="font-medium text-foreground/80">Duration:</span> {seconds}s
            </span>
          )}
          {typeof data?.status === 'string' && (
            <span>
              <span className="font-medium text-foreground/80">Status:</span> {data.status}
            </span>
          )}
        </div>
        {inputs.length > 0 && (
          <div>
            <span className="font-medium text-foreground/80">Simulated inputs:</span>
            <ul className="list-disc pl-4 mt-0.5 space-y-0.5 font-mono text-[11px]">
              {inputs.map((inp, idx) => (
                <li key={idx}>
                  {String(inp.id ?? '?')} &larr; {String(inp.value ?? '')}
                  {typeof inp.at_ms === 'number' ? ` @ ${inp.at_ms} ms` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {step.summary && (
          <div className={step.status === 'error' ? 'text-destructive' : undefined}>
            <span className="font-medium text-foreground/80">Result:</span> {step.summary}
          </div>
        )}
        {errors.length > 0 && (
          <pre className="rounded bg-amber-500/10 border border-amber-500/25 px-2.5 py-1.5 font-mono text-[11px] text-amber-800 dark:text-amber-300 overflow-x-auto whitespace-pre-wrap">
            {errors.join('\n')}
          </pre>
        )}
      </div>
    )
  }

  if (step.name === 'analyze_audio') {
    const dominantHz = typeof data?.dominant_hz === 'number' ? data.dominant_hz : null
    const peak = typeof data?.peak === 'number' ? data.peak : null
    const silent = data?.silent === true

    return (
      <div className="space-y-1.5 text-xs text-muted-foreground">
        {step.summary && (
          <div className={step.status === 'error' ? 'text-destructive' : undefined}>
            <span className="font-medium text-foreground/80">Result:</span> {step.summary}
          </div>
        )}
        {(dominantHz !== null || peak !== null || silent) && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px]">
            {silent ? (
              <span>silent</span>
            ) : (
              <>
                {dominantHz !== null && <span>dominant: {dominantHz} Hz</span>}
                {peak !== null && <span>peak: {peak}</span>}
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  const hasArgs = Object.keys(args).length > 0
  const hasData = !!data && Object.keys(data).length > 0

  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      {hasArgs && (
        <pre className="rounded bg-background/80 border border-border/70 px-2 py-1.5 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}
      {step.summary && (
        <div className={step.status === 'error' ? 'text-destructive' : undefined}>
          <span className="font-medium text-foreground/80">Result:</span> {step.summary}
        </div>
      )}
      {hasData && (
        <pre className="rounded bg-background/80 border border-border/70 px-2 py-1.5 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function AgentSteps({
  steps,
  isStreaming,
}: {
  steps: AgentStep[]
  isStreaming: boolean
}) {
  const [open, setOpen] = useState(isStreaming)
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({})
  const userToggledRef = useRef(false)
  const prevStreamingRef = useRef(isStreaming)

  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming && !userToggledRef.current) {
      setOpen(false)
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  if (steps.length === 0) return null

  const runningStep = [...steps].reverse().find(s => s.status === 'running')
  const countLabel = `${steps.length} ${steps.length === 1 ? 'step' : 'steps'}`
  const hasFailure = steps.some(s => s.status === 'error')

  function toggleGroup() {
    userToggledRef.current = true
    setOpen(prev => !prev)
  }

  function toggleStep(key: string) {
    setExpandedSteps(prev => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <div className="w-full rounded-xl border border-border/80 bg-muted/40 text-xs">
      <button
        type="button"
        onClick={toggleGroup}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
      >
        {open ? (
          <ChevronDown className="size-3.5 shrink-0" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0" />
        )}
        {runningStep ? (
          <Loader2 className="size-3.5 animate-spin shrink-0" />
        ) : hasFailure ? (
          <AlertCircle className="size-3.5 text-destructive shrink-0" />
        ) : (
          <Check className="size-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
        )}
        <span className="font-medium text-foreground/90 shrink-0">
          {runningStep ? activeVerb(runningStep) : countLabel}
        </span>
        <span className="truncate text-muted-foreground">
          {runningStep ? `(${countLabel})` : `· ${completedSummary(steps)}`}
        </span>
      </button>

      {open && (
        <ol className="border-t border-border/60 divide-y divide-border/50">
          {steps.map((step, index) => {
            const key = `${step.id}-${index}`
            const expanded = !!expandedSteps[key]
            const { action, target } = stepLabel(step)

            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => toggleStep(key)}
                  aria-expanded={expanded}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/70 transition-colors cursor-pointer"
                >
                  {expanded ? (
                    <ChevronDown className="size-3 text-muted-foreground shrink-0" />
                  ) : (
                    <ChevronRight className="size-3 text-muted-foreground shrink-0" />
                  )}
                  <StepStatusIcon step={step} />
                  <ToolIcon name={step.name} />
                  <span className="font-medium text-foreground/90 shrink-0">{action}</span>
                  {target && (
                    <span className="font-mono text-[11px] text-foreground/75 truncate max-w-[180px]">
                      {target}
                    </span>
                  )}
                  {step.summary && (
                    <span
                      className={`ml-auto truncate text-right ${
                        step.status === 'error'
                          ? 'text-destructive'
                          : isNonCleanRun(step)
                          ? 'text-amber-700 dark:text-amber-400'
                          : 'text-muted-foreground'
                      }`}
                    >
                      {step.summary}
                    </span>
                  )}
                </button>
                {expanded && (
                  <div className="px-8 pb-2.5 pt-1">
                    <StepDetails step={step} />
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
