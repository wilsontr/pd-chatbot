import { useEffect, useState } from 'react'
import { Loader2, Volume2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { AudioArtifact } from '../types'

function artifactFetchUrl(url: string): string {
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('/api/')) {
    return url
  }
  return `/api${url.startsWith('/') ? url : `/${url}`}`
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  return `${seconds.toFixed(1)}s`
}

export function PatchAudioPlayer({ audio }: { audio: AudioArtifact }) {
  const [status, setStatus] = useState<'loading' | 'ready' | 'expired' | 'error'>('loading')
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    const controller = new AbortController()
    setStatus('loading')
    setBlobUrl(null)

    fetch(artifactFetchUrl(audio.url), {
      headers: {
        'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '',
      },
      signal: controller.signal,
    })
      .then(async res => {
        if (cancelled) return
        if (res.status === 404) {
          setStatus('expired')
          return
        }
        if (!res.ok) {
          setStatus('error')
          return
        }
        const blob = await res.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setBlobUrl(objectUrl)
        setStatus('ready')
      })
      .catch(err => {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return
        setStatus('error')
      })

    return () => {
      cancelled = true
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [audio.id, audio.url, attempt])

  const duration = formatDuration(audio.seconds)

  return (
    <div className="rounded-lg border border-border bg-background/80 p-3 min-w-[240px] max-w-[300px] flex-1 space-y-2">
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5 font-medium text-foreground/90">
          <Volume2 className="size-3.5 text-muted-foreground shrink-0" />
          Rendered audio
        </span>
        {duration && <span className="font-mono text-[11px]">{duration}</span>}
      </div>

      {status === 'loading' && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-1.5">
          <Loader2 className="size-3.5 animate-spin shrink-0" />
          Loading audio…
        </div>
      )}

      {status === 'ready' && blobUrl && (
        <audio
          controls
          preload="metadata"
          src={blobUrl}
          className="w-full h-9"
          aria-label={`Rendered audio for ${audio.name}`}
        />
      )}

      {status === 'expired' && (
        <p className="text-xs text-muted-foreground">Audio clip expired.</p>
      )}

      {status === 'error' && (
        <div className="flex items-center justify-between gap-2 text-xs text-destructive">
          <span role="alert">Could not load audio.</span>
          <Button variant="outline" size="xs" onClick={() => setAttempt(n => n + 1)}>
            Retry
          </Button>
        </div>
      )}
    </div>
  )
}
