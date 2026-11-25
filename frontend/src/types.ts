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

export interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  messageId?: string
  patches?: PatchArtifact[]
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
