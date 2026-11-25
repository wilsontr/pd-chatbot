import type { PdPatch } from '../types'

/** Prefix an API-relative path the same way the audio player does. */
export function apiUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('/api/')) {
    return path
  }
  return `/api${path.startsWith('/') ? path : `/${path}`}`
}

async function save(blob: Blob, filename: string): Promise<boolean> {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
  return true
}

/** Convert a patch to a .pd file on the server and save it. */
export async function downloadPatch(patch: PdPatch): Promise<boolean> {
  try {
    const res = await fetch('/api/patch/pd', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '',
      },
      body: JSON.stringify(patch),
    })
    if (!res.ok) return false
    return save(await res.blob(), 'patch.pd')
  } catch {
    return false
  }
}

/**
 * Download a stored session patch's latest revision by id, so a patch edited in
 * a later turn still downloads as the newest version from an older message.
 */
export async function downloadPatchUrl(url: string, filename: string): Promise<boolean> {
  try {
    const res = await fetch(apiUrl(url), {
      headers: { 'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '' },
    })
    if (!res.ok) return false
    return save(await res.blob(), filename)
  } catch {
    return false
  }
}
