import type {
  AgentStep,
  AudioArtifact,
  HistoryItem,
  Message,
  PatchArtifact,
  Source,
} from '../types'

export type StreamEvent =
  | { type: 'meta'; sources: Source[]; query_type?: string }
  | { type: 'chunk'; text: string }
  | { type: 'text_reset' }
  | { type: 'tool_call'; id: string; name: string; arguments?: Record<string, unknown> }
  | {
      type: 'tool_result'
      id: string
      name: string
      ok: boolean
      summary: string
      chunk_count?: number
      data?: Record<string, unknown>
    }
  | { type: 'artifact'; kind: 'pd_patch'; data: PatchArtifact }
  | { type: 'artifact'; kind: 'audio'; data: AudioArtifact }
  | { type: 'artifact'; kind: string; data: unknown }
  | { type: 'done'; history: HistoryItem[]; message_id?: string | null }

function upsertByName<T extends { name: string }>(items: T[] | undefined, next: T): T[] {
  const list = items ?? []
  const idx = list.findIndex(item => item.name === next.name)
  if (idx === -1) return [...list, next]
  return [...list.slice(0, idx), next, ...list.slice(idx + 1)]
}

/** Finish any still-running steps when a stream ends cleanly or errors out. */
export function finalizeRunningSteps(
  steps: AgentStep[] | undefined,
  terminalStatus: 'done' | 'error',
): AgentStep[] | undefined {
  if (!steps || !steps.some(step => step.status === 'running')) return steps
  return steps.map(step =>
    step.status === 'running' ? { ...step, status: terminalStatus } : step,
  )
}

/** Pure reducer applying one SSE frame to the in-progress assistant message. */
export function applyStreamEvent(message: Message, event: StreamEvent): Message {
  switch (event.type) {
    case 'meta':
      return { ...message, sources: event.sources }
    case 'chunk':
      return { ...message, content: message.content + event.text }
    case 'text_reset':
      // The text so far preceded a tool call, so it was not the answer.
      return { ...message, content: '' }
    case 'tool_call': {
      const step: AgentStep = {
        id: event.id,
        name: event.name,
        arguments: event.arguments ?? {},
        status: 'running',
      }
      return { ...message, steps: [...(message.steps ?? []), step] }
    }
    case 'tool_result': {
      const steps = message.steps ?? []
      let idx = steps.findIndex(s => s.id === event.id)
      if (idx === -1) {
        for (let i = steps.length - 1; i >= 0; i -= 1) {
          if (steps[i].name === event.name && steps[i].status === 'running') {
            idx = i
            break
          }
        }
      }
      const patch: Partial<AgentStep> = {
        status: event.ok ? 'done' : 'error',
        summary: event.summary,
        chunkCount: event.chunk_count,
        data: event.data,
      }
      if (idx === -1) {
        return {
          ...message,
          steps: [
            ...steps,
            {
              id: event.id,
              name: event.name,
              arguments: {},
              status: event.ok ? 'done' : 'error',
              summary: event.summary,
              chunkCount: event.chunk_count,
              data: event.data,
            },
          ],
        }
      }
      const updated = { ...steps[idx], ...patch }
      return {
        ...message,
        steps: [...steps.slice(0, idx), updated, ...steps.slice(idx + 1)],
      }
    }
    case 'artifact':
      if (event.kind === 'pd_patch') {
        return {
          ...message,
          patches: upsertByName(message.patches, event.data as PatchArtifact),
        }
      }
      if (event.kind === 'audio') {
        return {
          ...message,
          audio: upsertByName(message.audio, event.data as AudioArtifact),
        }
      }
      return message
    case 'done':
      return {
        ...message,
        messageId: event.message_id ?? undefined,
        steps: finalizeRunningSteps(message.steps, 'done'),
      }
    default:
      return message
  }
}
