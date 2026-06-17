import { layoutPatch, computeViewBox, estimateBoxWidth, BOX_H } from '../lib/pdLayout'
import type { PdPatch, PdObject } from '../types'

const NUB_W = 7
const NUB_H = 3
const BOX_PAD_X = 4
const CORNER_CUT = 6  // 45° cut size for msg and floatatom boxes
const FONT_SIZE = 12

// X coordinate of the left edge of nub i (out of count total)
function nubLeft(boxX: number, boxW: number, i: number, count: number): number {
  if (count <= 1) return boxX
  return boxX + i * (boxW - NUB_W) / (count - 1)
}

// X coordinate of the center of nub i
function nubCenterX(boxX: number, boxW: number, i: number, count: number): number {
  return nubLeft(boxX, boxW, i, count) + NUB_W / 2
}

// SVG polygon points for each box type
function boxPoints(x: number, y: number, w: number, h: number, type: PdObject['type']): string {
  const pts = (coords: [number, number][]) =>
    coords.map(([px, py]) => `${px},${py}`).join(' ')
  const C = CORNER_CUT
  if (type === 'msg') {
    // Full-width corners with small inward tails to a flat inset section —
    // majority of the right edge is the flat inset part, matching Pd's message box shape
    const T = 4  // tail height in px
    return pts([[x, y], [x+w, y], [x+w-C, y+T], [x+w-C, y+h-T], [x+w, y+h], [x, y+h]])
  }
  if (type === 'floatatom') {
    // Rectangle with top-right corner cut at 45° (Pd number box shape)
    return pts([[x, y], [x+w-C, y], [x+w, y+C], [x+w, y+h], [x, y+h]])
  }
  // Plain rectangle for obj (and fallback)
  return pts([[x, y], [x+w, y], [x+w, y+h], [x, y+h]])
}

function parsePatch(json: string): { patch: PdPatch } | { error: string } {
  let p: unknown
  try {
    p = JSON.parse(json)
  } catch {
    return { error: 'truncated' }
  }
  if (!p || typeof p !== 'object') return { error: 'invalid' }
  const raw = p as Record<string, unknown>
  if (!Array.isArray(raw.objects) || !Array.isArray(raw.connections)) {
    return { error: 'invalid' }
  }
  const patch = raw as unknown as PdPatch

  // Drop objects that appear in no connections — they are LLM omissions.
  // Comments and known UI objects are intentionally standalone and are kept regardless.
  // Objects that are intentionally standalone — either UI widgets with no wires,
  // or named data stores accessed by name (table, array) rather than by connection.
  const PD_UI_OBJECTS = new Set(['bng', 'tgl', 'hsl', 'vsl', 'hradio', 'vradio', 'vu', 'cnv', 'nbx', 'table', 'array'])
  const connected = new Set<string>()
  for (const c of patch.connections) {
    connected.add(c.srcId)
    connected.add(c.dstId)
  }
  const objects = patch.objects.filter(o => {
    if (o.type === 'comment') return true
    if (connected.has(o.id)) return true
    const objName = o.text?.trim().split(/\s+/)[0]
    return PD_UI_OBJECTS.has(objName)
  })
  return { patch: { ...patch, objects } }
}

export function PdPatchViewer({ json }: { json: string }) {
  const result = parsePatch(json)

  if ('error' in result) {
    return (
      <div className="my-3 px-3 py-2 rounded border border-amber-200/60 bg-amber-50/40 text-xs text-amber-800/80 dark:border-amber-700/40 dark:bg-amber-900/20 dark:text-amber-400/80">
        Patch diagram could not be rendered — the response may have been cut off.
      </div>
    )
  }

  const { patch } = result
  if (patch.objects.length === 0) return null

  const layout = layoutPatch(patch)
  const { w: svgW, h: svgH } = computeViewBox(layout)
  const objMap = new Map(patch.objects.map(o => [o.id, o]))

  return (
    <div className="overflow-x-auto my-3 rounded border border-black/20 inline-block max-w-full" data-patch-json={json}>
      <svg
        width={svgW}
        height={svgH}
        viewBox={`0 0 ${svgW} ${svgH}`}
        style={{ display: 'block', backgroundColor: '#e8e8e8' }}
        aria-label="Pd patch diagram"
      >
        {/* Wires — drawn first so boxes render on top */}
        {patch.connections.map((conn, i) => {
          const src = layout.get(conn.srcId)
          const dst = layout.get(conn.dstId)
          const srcObj = objMap.get(conn.srcId)
          const dstObj = objMap.get(conn.dstId)
          if (!src || !dst || !srcObj || !dstObj) return null
          const x1 = nubCenterX(src.x, src.w, conn.srcOutlet, srcObj.outlets)
          const y1 = src.y + BOX_H + NUB_H
          const x2 = nubCenterX(dst.x, dst.w, conn.dstInlet, dstObj.inlets)
          const y2 = dst.y - NUB_H
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#000" strokeWidth={1.5} />
        })}

        {/* Objects */}
        {patch.objects.map(obj => {
          const pos = layout.get(obj.id)
          if (!pos) return null
          const { x, y, w } = pos

          if (obj.type === 'comment') {
            return (
              <text
                key={obj.id}
                x={x}
                y={y + FONT_SIZE}
                fill="#000"
                fontFamily='"Courier New", Courier, monospace'
                fontSize={FONT_SIZE}
              >
                {obj.text}
              </text>
            )
          }

          return (
            <g key={obj.id}>
              <polygon
                points={boxPoints(x, y, w, BOX_H, obj.type)}
                fill="#dfdfdf"
                stroke="#000"
                strokeWidth={1}
              />
              <text
                x={x + BOX_PAD_X}
                y={y + BOX_H / 2 + FONT_SIZE * 0.35}
                fill="#000"
                fontFamily='"Courier New", Courier, monospace'
                fontSize={FONT_SIZE}
              >
                {obj.text}
              </text>
              {/* Inlet nubs — protrude upward from top edge */}
              {Array.from({ length: obj.inlets }, (_, i) => (
                <rect
                  key={`in${i}`}
                  x={nubLeft(x, w, i, obj.inlets)}
                  y={y - NUB_H}
                  width={NUB_W}
                  height={NUB_H}
                  fill="#000"
                />
              ))}
              {/* Outlet nubs — protrude downward from bottom edge */}
              {Array.from({ length: obj.outlets }, (_, i) => (
                <rect
                  key={`out${i}`}
                  x={nubLeft(x, w, i, obj.outlets)}
                  y={y + BOX_H}
                  width={NUB_W}
                  height={NUB_H}
                  fill="#000"
                />
              ))}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
