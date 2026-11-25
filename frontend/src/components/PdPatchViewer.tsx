import { useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { downloadPatch } from '../lib/pdDownload'
import { layoutPatch, computeViewBox, graphicLabel } from '../lib/pdLayout'
import type { LayoutNode } from '../lib/pdLayout'
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
  if (type === 'floatatom' || type === 'nbx') {
    // Rectangle with top-right corner cut at 45° (Pd number box shape)
    return pts([[x, y], [x+w-C, y], [x+w, y+C], [x+w, y+h], [x, y+h]])
  }
  // Plain rectangle for obj, table, the graphic widgets (and fallback)
  return pts([[x, y], [x+w, y], [x+w, y+h], [x, y+h]])
}

// Interior of the graphic widgets, drawn over the body outline
function WidgetFace({ type, pos: { x, y, w, h } }: { type: PdObject['type']; pos: LayoutNode }) {
  const line = { stroke: '#000', strokeWidth: 1 }
  const cells = Array.from({ length: 8 }, (_, i) => i)
  switch (type) {
    case 'bng':
      return <circle cx={x + w / 2} cy={y + h / 2} r={w / 2 - 2} fill="none" {...line} />
    case 'tgl':
      return <path d={`M${x + 3},${y + 3}L${x + w - 3},${y + h - 3}M${x + w - 3},${y + 3}L${x + 3},${y + h - 3}`} {...line} />
    case 'hsl':
      return <line x1={x + w / 4} y1={y + 2} x2={x + w / 4} y2={y + h - 2} stroke="#000" strokeWidth={3} />
    case 'vsl':
      return <line x1={x + 2} y1={y + h * 0.75} x2={x + w - 2} y2={y + h * 0.75} stroke="#000" strokeWidth={3} />
    case 'hradio':
      return <>
        {cells.slice(1).map(i => <line key={i} x1={x + i * w / 8} y1={y} x2={x + i * w / 8} y2={y + h} {...line} />)}
        <rect x={x + 4} y={y + 4} width={w / 8 - 8} height={h - 8} fill="#000" />
      </>
    case 'vradio':
      return <>
        {cells.slice(1).map(i => <line key={i} x1={x} y1={y + i * h / 8} x2={x + w} y2={y + i * h / 8} {...line} />)}
        <rect x={x + 4} y={y + 4} width={w - 8} height={h / 8 - 8} fill="#000" />
      </>
    case 'vu':
      return <>
        {cells.map(i => (
          <rect key={i} x={x + 4} y={y + 4 + i * (h - 8) / 8} width={w - 8} height={(h - 8) / 8 - 2}
            fill={i === 0 ? '#c33' : i < 3 ? '#cb3' : '#3a3'} />
        ))}
      </>
    case 'array':
      return <path d={`M${x},${y + h / 2}Q${x + w / 4},${y - h * 0.3} ${x + w / 2},${y + h / 2}T${x + w},${y + h / 2}`} fill="none" {...line} />
    default:
      return null
  }
}

function boxFill(type: PdObject['type']): string {
  if (type === 'cnv') return '#c8c8c8'
  return type === 'array' ? '#fff' : '#dfdfdf'
}

function DownloadButton({ patch }: { patch: PdPatch }) {
  const [state, setState] = useState<'idle' | 'busy' | 'failed'>('idle')

  async function handleClick() {
    setState('busy')
    setState(await downloadPatch(patch) ? 'idle' : 'failed')
  }

  return (
    <div className="flex items-center gap-2 mt-1">
      <Button variant="outline" size="xs" disabled={state === 'busy'} onClick={handleClick}>
        <Download /> Download .pd
      </Button>
      {state === 'failed' && (
        <span role="alert" className="text-xs text-destructive">Download failed. Please try again.</span>
      )}
    </div>
  )
}

export function PdPatchViewer({ patch }: { patch: PdPatch }) {
  const layout = layoutPatch(patch)
  const { w: svgW, h: svgH } = computeViewBox(layout)
  const objMap = new Map(patch.objects.map(o => [o.id, o]))

  return (
    <div className="min-w-0 max-w-full">
    <div className="overflow-x-auto rounded border border-black/20 inline-block max-w-full">
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
          const y1 = src.y + src.h + NUB_H
          const x2 = nubCenterX(dst.x, dst.w, conn.dstInlet, dstObj.inlets)
          const y2 = dst.y - NUB_H
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#000" strokeWidth={1.5} />
        })}

        {/* Objects */}
        {patch.objects.map(obj => {
          const pos = layout.get(obj.id)
          if (!pos) return null
          const { x, y, w, h } = pos
          const label = graphicLabel(obj)

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
                points={boxPoints(x, y, w, h, obj.type)}
                fill={boxFill(obj.type)}
                stroke="#000"
                strokeWidth={1}
              />
              <WidgetFace type={obj.type} pos={pos} />
              <text
                x={label ? pos.labelX : x + BOX_PAD_X}
                y={y + h / 2 + FONT_SIZE * 0.35}
                fill="#000"
                fontFamily='"Courier New", Courier, monospace'
                fontSize={FONT_SIZE}
              >
                {label || obj.text}
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
                  y={y + h}
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
    <DownloadButton patch={patch} />
    </div>
  )
}
