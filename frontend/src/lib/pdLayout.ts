import dagre from 'dagre'
import type { PdObject, PdObjectType, PdPatch } from '../types'

const CHAR_W = 7.2    // Courier New 12px, monospace width per character
const BOX_PAD_X = 4   // horizontal padding inside box
export const BOX_H = 20
const MIN_BOX_W = 36
const MSG_NOTCH_DEPTH = 6  // matches CORNER_CUT in PdPatchViewer — extra right clearance for msg boxes

export function estimateBoxWidth(text: string | undefined, type?: string): number {
  const rightExtra = type === 'msg' ? MSG_NOTCH_DEPTH : 0
  return Math.max(MIN_BOX_W, Math.ceil((text ?? '').length * CHAR_W) + BOX_PAD_X * 2 + rightExtra)
}

// Fixed body sizes (px) for types drawn as a graphic rather than a text box.
// Creation arguments that set a size in Pd are not honoured: diagrams are schematic.
const GRAPHIC_SIZE: Partial<Record<PdObjectType, { w: number; h: number }>> = {
  bng: { w: 20, h: 20 },
  tgl: { w: 20, h: 20 },
  hsl: { w: 130, h: 20 },
  vsl: { w: 20, h: 100 },
  hradio: { w: 128, h: 16 },
  vradio: { w: 16, h: 128 },
  vu: { w: 20, h: 100 },
  cnv: { w: 100, h: 60 },
  array: { w: 140, h: 80 },
}
const LABEL_GAP = 6

// Label drawn beside a graphic body: the creation text, since the body can't show it.
export function graphicLabel(obj: PdObject): string {
  return GRAPHIC_SIZE[obj.type] ? (obj.text ?? '').trim() || obj.type : ''
}

export function boxSize(obj: PdObject): { w: number; h: number } {
  return GRAPHIC_SIZE[obj.type] ?? { w: estimateBoxWidth(obj.text, obj.type), h: BOX_H }
}

// Width including the label, so neighbours don't overlap it.
function footprintWidth(obj: PdObject): number {
  const label = graphicLabel(obj)
  const { w } = boxSize(obj)
  return label ? w + LABEL_GAP + Math.ceil(label.length * CHAR_W) : w
}

export interface LayoutNode {
  x: number  // top-left
  y: number
  w: number
  h: number
  labelX: number     // left edge of a graphic's label
  footprintW: number // w plus any label
}

export function layoutPatch(patch: PdPatch): Map<string, LayoutNode> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 40, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))

  for (const obj of patch.objects) {
    g.setNode(obj.id, { width: footprintWidth(obj), height: boxSize(obj).h })
  }
  for (const conn of patch.connections) {
    if (conn.srcId && conn.dstId) g.setEdge(conn.srcId, conn.dstId)
  }

  dagre.layout(g)

  const result = new Map<string, LayoutNode>()
  for (const obj of patch.objects) {
    const node = g.node(obj.id)
    if (node) {
      const { w, h } = boxSize(obj)
      const x = node.x - node.width / 2
      result.set(obj.id, {
        x,
        y: node.y - node.height / 2,
        w,
        h,
        labelX: x + w + LABEL_GAP,
        footprintW: node.width,
      })
    }
  }
  return result
}

export function computeViewBox(layout: Map<string, LayoutNode>): { w: number; h: number } {
  let maxX = 0, maxY = 0
  for (const n of layout.values()) {
    maxX = Math.max(maxX, n.x + n.footprintW)
    maxY = Math.max(maxY, n.y + n.h)
  }
  return { w: maxX + 20, h: maxY + 20 }
}
