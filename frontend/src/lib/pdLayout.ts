import dagre from 'dagre'
import type { PdPatch } from '../types'

const CHAR_W = 7.2    // Courier New 12px, monospace width per character
const BOX_PAD_X = 4   // horizontal padding inside box
export const BOX_H = 20
const MIN_BOX_W = 36

export function estimateBoxWidth(text: string): number {
  return Math.max(MIN_BOX_W, Math.ceil(text.length * CHAR_W) + BOX_PAD_X * 2)
}

export interface LayoutNode {
  x: number  // top-left
  y: number
  w: number
  h: number
}

export function layoutPatch(patch: PdPatch): Map<string, LayoutNode> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 40, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))

  for (const obj of patch.objects) {
    g.setNode(obj.id, { width: estimateBoxWidth(obj.text), height: BOX_H })
  }
  for (const conn of patch.connections) {
    if (conn.srcId && conn.dstId) g.setEdge(conn.srcId, conn.dstId)
  }

  dagre.layout(g)

  const result = new Map<string, LayoutNode>()
  for (const obj of patch.objects) {
    const node = g.node(obj.id)
    if (node) {
      result.set(obj.id, {
        x: node.x - node.width / 2,
        y: node.y - node.height / 2,
        w: node.width,
        h: BOX_H,
      })
    }
  }
  return result
}

export function computeViewBox(layout: Map<string, LayoutNode>): { w: number; h: number } {
  let maxX = 0, maxY = 0
  for (const n of layout.values()) {
    maxX = Math.max(maxX, n.x + n.w)
    maxY = Math.max(maxY, n.y + n.h)
  }
  return { w: maxX + 20, h: maxY + 20 }
}
