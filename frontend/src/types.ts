export interface Source {
  heading_path: string
  url: string
  source: 'msp_manual' | 'iem_reference' | 'puckette_book'
  content_type: 'conceptual' | 'object_reference'
  object_name: string | null
}

/** A patch the agent wrote, drawn under its answer. */
export interface PatchArtifact {
  name: string
  patch: PdPatch
}

/** A rendered audio clip from a sandboxed Pd run, paired with a patch by name. */
export interface AudioArtifact {
  id: string
  url: string
  name: string
  mime: string
  seconds: number
}

/** One tool call and its result from an agent turn. */
export interface AgentStep {
  id: string
  name: string
  arguments: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  summary?: string
  chunkCount?: number
  data?: Record<string, unknown>
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  messageId?: string
  runId?: string
  sessionId?: string
  langfuseTraceUrl?: string
  patches?: PatchArtifact[]
  audio?: AudioArtifact[]
  steps?: AgentStep[]
}

export interface TokenUsage {
  input?: number
  output?: number
  cache_read?: number
  cache_write?: number
  total?: number
  [key: string]: number | undefined
}

export interface RunSummary {
  run_id: string
  session_id: string
  question: string
  interface: string
  status: 'running' | 'completed' | 'failed' | 'refused' | string
  query_type: string
  answer: string
  model: string
  turns: number
  tool_calls: number
  total_tokens: number
  cost_usd: number | null
  latency_ms: number | null
  stop_reason: string | null
  budget_exceeded: string | null
  refused: boolean
  cached: boolean
  langfuse_trace_id: string | null
  langfuse_trace_url: string | null
  created_at: string | null
  completed_at: string | null
}

export interface StoredRunEvent {
  seq: number
  run_id: string
  session_id: string
  event_type:
    | 'model_turn'
    | 'tool_call'
    | 'tool_result'
    | 'sources'
    | 'text_delta'
    | 'text_reset'
    | 'artifact'
    | 'done'
    | string
  payload: Record<string, unknown>
  created_at: string | null
}

export interface RunDetail extends RunSummary {
  events: StoredRunEvent[]
}

export interface HistoryItem {
  role: 'user' | 'assistant'
  content: string
}

// Must match PdObjectType in pd_schema.py (checked by tests/test_pd_schema.py).
// `text` is the box's Pd creation text; for a widget or data store it starts with
// the type name, e.g. 'hsl 128 15 0 127' or 'table wave 512'.
export type PdObjectType =
  | 'obj' | 'msg' | 'floatatom' | 'comment'
  // Named data stores
  | 'table' | 'array'
  // UI widgets
  | 'bng' | 'tgl' | 'hsl' | 'vsl' | 'hradio' | 'vradio' | 'nbx' | 'vu' | 'cnv'

export interface PdObject {
  id: string
  type: PdObjectType
  text: string
  inlets: number
  outlets: number
}

export interface PdConnection {
  srcId: string
  srcOutlet: number
  dstId: string
  dstInlet: number
}

export interface PdPatch {
  objects: PdObject[]
  connections: PdConnection[]
}
