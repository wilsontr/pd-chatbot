import type {
  AudioArtifact,
  PatchArtifact,
  RunDetail,
  Source,
  StoredRunEvent,
  TokenUsage,
} from '../types'

export type TimelineStepKind =
  | 'model_turn'
  | 'tool_call'
  | 'tool_result'
  | 'sources'
  | 'text_output'
  | 'text_reset'
  | 'artifact'
  | 'done'

export interface TimelineStep {
  id: string
  index: number
  kind: TimelineStepKind
  seqStart: number
  seqEnd: number
  title: string
  subtitle?: string
  createdAt: string | null
  offsetMs: number | null
  tokens: number
  tokenBreakdown?: TokenUsage
  costUsd: number
  latencyMs: number
  status?: 'ok' | 'warning' | 'error' | 'info'
  // Kind-specific payload fields
  model?: string
  turn?: number
  stopReason?: string | null
  text?: string
  deltaCount?: number
  contentBlocks?: Record<string, unknown>[]
  toolId?: string
  toolName?: string
  toolArguments?: Record<string, unknown>
  toolSummary?: string
  toolContent?: string
  toolData?: Record<string, unknown>
  toolChunks?: Record<string, unknown>[]
  sources?: Source[]
  queryType?: string
  artifactKind?: string
  patchArtifact?: PatchArtifact
  audioArtifact?: AudioArtifact
  artifactData?: Record<string, unknown>
  doneMeta?: {
    turns: number
    toolCalls: number
    refused: boolean
    cached: boolean
    patchFailed: boolean
    budgetExceeded: string | null
  }
}

export interface CumulativeMetrics {
  tokens: number
  costUsd: number
  latencyMs: number
  modelTurns: number
  toolCalls: number
  artifacts: number
}

function parseMs(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = Date.parse(iso)
  return Number.isFinite(ms) ? ms : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function computeUsageTotal(usage: TokenUsage): number {
  const buckets =
    (asNumber(usage.input) ?? 0) +
    (asNumber(usage.output) ?? 0) +
    (asNumber(usage.cache_read) ?? 0) +
    (asNumber(usage.cache_write) ?? 0)
  if (buckets > 0) return buckets
  return asNumber(usage.total) ?? 0
}

function toolTargetLabel(name: string, args: Record<string, unknown>): string | undefined {
  if (name === 'search_docs') {
    const q = asString(args.query).trim()
    return q ? `"${q}"` : undefined
  }
  const patchName = asString(args.name).trim()
  if (patchName) {
    const secs = asNumber(args.seconds)
    return name === 'run_patch' && secs ? `${patchName} (${secs}s)` : patchName
  }
  return undefined
}

function toolActionLabel(name: string): string {
  switch (name) {
    case 'search_docs':
      return 'Search docs'
    case 'write_patch':
      return 'Write patch'
    case 'run_patch':
      return 'Run patch'
    case 'analyze_audio':
      return 'Analyze audio'
    default:
      return name || 'Tool'
  }
}

function timestampDeltaMs(prevMs: number | null, currMs: number | null): number {
  if (prevMs === null || currMs === null) return 0
  return Math.max(0, Math.round((currMs - prevMs) * 100) / 100)
}

/**
 * Convert a stored run's ordered event log into replayable timeline steps.
 *
 * Preserves exact event order for model turns, tool calls, tool results,
 * sources, text resets, artifacts, and done. Consecutive `text_delta` events
 * are collapsed into a single `text_output` step (keeping `seqStart..seqEnd`,
 * `deltaCount`, and streaming duration) so multi-hundred-token replies stay
 * scannable while remaining faithful to the stored sequence.
 */
export function buildTimelineSteps(run: RunDetail): TimelineStep[] {
  const events: StoredRunEvent[] = run.events ?? []
  const runStartMs = parseMs(run.created_at) ?? parseMs(events[0]?.created_at)
  const steps: TimelineStep[] = []
  let prevEventMs: number | null = runStartMs

  let i = 0
  while (i < events.length) {
    const ev = events[i]
    const payload = asRecord(ev.payload)
    const evMs = parseMs(ev.created_at)
    const offsetMs =
      runStartMs !== null && evMs !== null ? Math.max(0, Math.round(evMs - runStartMs)) : null
    const index = steps.length

    if (ev.event_type === 'text_delta') {
      const startSeq = ev.seq
      let endSeq = ev.seq
      const firstMs = evMs
      let lastMs = evMs
      const parts: string[] = [asString(payload.text)]
      let count = 1

      while (i + 1 < events.length && events[i + 1].event_type === 'text_delta') {
        i += 1
        const nextEv = events[i]
        const nextPayload = asRecord(nextEv.payload)
        parts.push(asString(nextPayload.text))
        endSeq = nextEv.seq
        const nextMs = parseMs(nextEv.created_at)
        if (nextMs !== null) lastMs = nextMs
        count += 1
      }

      const fullText = parts.join('')
      const spanMs = timestampDeltaMs(firstMs, lastMs)
      steps.push({
        id: `step-${startSeq}`,
        index,
        kind: 'text_output',
        seqStart: startSeq,
        seqEnd: endSeq,
        title: 'Answer stream',
        subtitle: `${count} ${count === 1 ? 'chunk' : 'chunks'} · ${fullText.length} chars`,
        createdAt: ev.created_at,
        offsetMs,
        tokens: 0,
        costUsd: 0,
        latencyMs: spanMs,
        status: 'info',
        text: fullText,
        deltaCount: count,
      })
      prevEventMs = lastMs ?? prevEventMs
      i += 1
      continue
    }

    if (ev.event_type === 'model_turn') {
      const turnNum = asNumber(payload.turn) ?? index + 1
      const model = asString(payload.model) || run.model
      const usage = asRecord(payload.usage) as TokenUsage
      const tokens = computeUsageTotal(usage)
      const costUsd = asNumber(payload.cost_usd) ?? 0
      const latencyMs = asNumber(payload.latency_ms) ?? timestampDeltaMs(prevEventMs, evMs)
      const stopReason = typeof payload.stop_reason === 'string' ? payload.stop_reason : null
      const contentBlocks = Array.isArray(payload.content)
        ? payload.content.filter((b): b is Record<string, unknown> => typeof b === 'object' && b !== null)
        : []
      const toolUses = contentBlocks.filter(b => b.type === 'tool_use')
      const subtitleParts: string[] = []
      if (model) subtitleParts.push(model)
      if (stopReason) subtitleParts.push(stopReason)
      if (toolUses.length > 0) {
        subtitleParts.push(
          `${toolUses.length} ${toolUses.length === 1 ? 'tool call' : 'tool calls'}`,
        )
      }

      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'model_turn',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title: `Model turn #${turnNum}`,
        subtitle: subtitleParts.join(' · ') || undefined,
        createdAt: ev.created_at,
        offsetMs,
        tokens,
        tokenBreakdown: usage,
        costUsd,
        latencyMs,
        status: 'ok',
        model,
        turn: turnNum,
        stopReason,
        text: asString(payload.text),
        contentBlocks,
      })
    } else if (ev.event_type === 'tool_call') {
      const name = asString(payload.name)
      const args = asRecord(payload.arguments)
      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'tool_call',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title: `Call ${toolActionLabel(name)}`,
        subtitle: toolTargetLabel(name, args),
        createdAt: ev.created_at,
        offsetMs,
        tokens: 0,
        costUsd: 0,
        latencyMs: 0,
        status: 'info',
        toolId: asString(payload.id),
        toolName: name,
        toolArguments: args,
      })
    } else if (ev.event_type === 'tool_result') {
      const name = asString(payload.name)
      const ok = payload.ok !== false
      const data = asRecord(payload.data)
      const nonCleanPatch = ok && name === 'run_patch' && data.ran_cleanly === false
      const chunks = Array.isArray(payload.chunks)
        ? payload.chunks.filter((c): c is Record<string, unknown> => typeof c === 'object' && c !== null)
        : []
      const latencyMs = asNumber(payload.latency_ms) ?? timestampDeltaMs(prevEventMs, evMs)
      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'tool_result',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title: `Result · ${toolActionLabel(name)}`,
        subtitle: asString(payload.summary) || (ok ? 'ok' : 'failed'),
        createdAt: ev.created_at,
        offsetMs,
        tokens: 0,
        costUsd: 0,
        latencyMs,
        status: !ok ? 'error' : nonCleanPatch ? 'warning' : 'ok',
        toolId: asString(payload.id),
        toolName: name,
        toolSummary: asString(payload.summary),
        toolContent: asString(payload.content),
        toolData: data,
        toolChunks: chunks,
      })
    } else if (ev.event_type === 'sources') {
      const sources = Array.isArray(payload.sources)
        ? (payload.sources as Source[])
        : []
      const queryType = asString(payload.query_type)
      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'sources',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title: 'Sources',
        subtitle: `${sources.length} ${sources.length === 1 ? 'citation' : 'citations'}${queryType ? ` · ${queryType}` : ''}`,
        createdAt: ev.created_at,
        offsetMs,
        tokens: 0,
        costUsd: 0,
        latencyMs: 0,
        status: 'info',
        sources,
        queryType,
      })
    } else if (ev.event_type === 'text_reset') {
      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'text_reset',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title: 'Text reset',
        subtitle: 'Discarded draft text emitted before a tool call',
        createdAt: ev.created_at,
        offsetMs,
        tokens: 0,
        costUsd: 0,
        latencyMs: 0,
        status: 'warning',
      })
    } else if (ev.event_type === 'artifact') {
      const kind = asString(payload.kind)
      const data = asRecord(payload.data)
      const artifactName = asString(data.name)
      const patchArtifact = kind === 'pd_patch' ? (data as unknown as PatchArtifact) : undefined
      const audioArtifact = kind === 'audio' ? (data as unknown as AudioArtifact) : undefined
      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'artifact',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title:
          kind === 'pd_patch'
            ? 'Patch artifact'
            : kind === 'audio'
            ? 'Audio artifact'
            : `Artifact · ${kind || 'output'}`,
        subtitle: artifactName || undefined,
        createdAt: ev.created_at,
        offsetMs,
        tokens: 0,
        costUsd: 0,
        latencyMs: 0,
        status: 'ok',
        artifactKind: kind,
        patchArtifact,
        audioArtifact,
        artifactData: data,
      })
    } else if (ev.event_type === 'done') {
      const refused = payload.refused === true
      const cached = payload.cached === true
      const patchFailed = payload.patch_failed === true
      const budgetExceeded =
        typeof payload.budget_exceeded === 'string' ? payload.budget_exceeded : null
      const flags: string[] = []
      if (cached) flags.push('cached')
      if (refused) flags.push('refused')
      if (budgetExceeded) flags.push(`budget: ${budgetExceeded}`)
      if (patchFailed) flags.push('patch invalid')
      if (flags.length === 0) flags.push(run.status || 'completed')

      steps.push({
        id: `step-${ev.seq}`,
        index,
        kind: 'done',
        seqStart: ev.seq,
        seqEnd: ev.seq,
        title: 'Run finished',
        subtitle: flags.join(' · '),
        createdAt: ev.created_at,
        offsetMs,
        tokens: run.total_tokens ?? 0,
        costUsd: run.cost_usd ?? 0,
        latencyMs: run.latency_ms ?? timestampDeltaMs(runStartMs, evMs),
        status:
          run.status === 'failed' || patchFailed
            ? 'error'
            : refused || budgetExceeded
            ? 'warning'
            : 'ok',
        text: asString(payload.answer) || run.answer,
        doneMeta: {
          turns: asNumber(payload.turns) ?? run.turns,
          toolCalls: asNumber(payload.tool_calls) ?? run.tool_calls,
          refused,
          cached,
          patchFailed,
          budgetExceeded,
        },
      })
    }

    prevEventMs = evMs ?? prevEventMs
    i += 1
  }

  return steps
}

/**
 * Sum step-level tokens, cost, and latency up through the first `visibleCount` steps.
 * Excludes the terminal `done` summary step from summing so totals aren't double-counted.
 */
export function cumulativeMetrics(
  steps: TimelineStep[],
  visibleCount: number,
): CumulativeMetrics {
  const limit = Math.max(0, Math.min(visibleCount, steps.length))
  let tokens = 0
  let costUsd = 0
  let latencyMs = 0
  let modelTurns = 0
  let toolCalls = 0
  let artifacts = 0

  for (let i = 0; i < limit; i += 1) {
    const step = steps[i]
    if (step.kind === 'done') continue
    tokens += step.tokens
    costUsd += step.costUsd
    latencyMs += step.latencyMs
    if (step.kind === 'model_turn') modelTurns += 1
    else if (step.kind === 'tool_call') toolCalls += 1
    else if (step.kind === 'artifact') artifacts += 1
  }

  return {
    tokens,
    costUsd: Math.round(costUsd * 1e8) / 1e8,
    latencyMs: Math.round(latencyMs * 100) / 100,
    modelTurns,
    toolCalls,
    artifacts,
  }
}

export function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined || !Number.isFinite(tokens)) return '0 tok'
  return `${Math.round(tokens).toLocaleString('en-US')} tok`
}

export function formatCost(costUsd: number | null | undefined): string {
  if (costUsd === null || costUsd === undefined || !Number.isFinite(costUsd) || costUsd <= 0) {
    return '$0.00'
  }
  if (costUsd < 0.0001) return `$${costUsd.toFixed(6)}`
  if (costUsd < 0.01) return `$${costUsd.toFixed(4)}`
  return `$${costUsd.toFixed(3)}`
}

export function formatLatency(latencyMs: number | null | undefined): string {
  if (latencyMs === null || latencyMs === undefined || !Number.isFinite(latencyMs)) {
    return '0 ms'
  }
  if (latencyMs < 1000) return `${Math.round(latencyMs)} ms`
  return `${(latencyMs / 1000).toFixed(2)} s`
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
