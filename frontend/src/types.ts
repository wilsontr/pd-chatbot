export interface Source {
  heading_path: string
  url: string
  source: 'msp_manual' | 'iem_reference' | 'puckette_book'
  content_type: 'conceptual' | 'object_reference'
  object_name: string | null
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
}

export interface HistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export type PdObjectType = 'obj' | 'msg' | 'floatatom' | 'comment'

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
