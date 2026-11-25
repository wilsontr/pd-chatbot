import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiUrl, downloadPatchUrl } from '../lib/pdDownload'
import type { SessionPatch } from '../types'

/**
 * The patches stored for this conversation. Every patch keeps its id across
 * edits, so its button always downloads the latest revision — including from
 * an answer written several turns ago.
 */
export function SessionPatches({
  sessionId,
  version,
}: {
  sessionId: string | null
  /** Changes when an answer completes, which is when the server finished saving. */
  version: number
}) {
  const [patches, setPatches] = useState<SessionPatch[]>([])
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    if (!sessionId) {
      setPatches([])
      return
    }
    const controller = new AbortController()
    fetch(apiUrl(`/sessions/${encodeURIComponent(sessionId)}/patches`), {
      headers: { 'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '' },
      signal: controller.signal,
    })
      .then(res => (res.ok ? res.json() : null))
      .then((data: { patches?: SessionPatch[] } | null) => {
        if (data?.patches) setPatches(data.patches)
      })
      .catch(err => {
        if (err instanceof DOMException && err.name === 'AbortError') return
      })
    return () => controller.abort()
  }, [sessionId, version])

  if (!sessionId || patches.length === 0) return null

  async function handleDownload(patch: SessionPatch) {
    setFailed(await downloadPatchUrl(patch.pd_url, `${patch.name}.pd`) ? null : patch.id)
  }

  return (
    <div className="border-t border-[#d0d0d0] dark:border-[#3a3a3a] bg-[#fafafa] dark:bg-[#232323] px-4 py-1.5 shrink-0">
      <div className="max-w-[816px] mx-auto flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium text-muted-foreground">
          Patches in this session
        </span>
        {patches.map(patch => (
          <Button
            key={patch.id}
            variant="outline"
            size="xs"
            onClick={() => handleDownload(patch)}
            title={`Download ${patch.name}.pd (revision ${patch.revision})`}
          >
            <Download />
            {patch.name}
            <span className="text-muted-foreground">rev {patch.revision}</span>
          </Button>
        ))}
        {failed && (
          <span role="alert" className="text-xs text-destructive">
            Could not download that patch.
          </span>
        )}
      </div>
    </div>
  )
}
