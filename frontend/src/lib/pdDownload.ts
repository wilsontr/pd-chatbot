import type { PdPatch } from '../types'

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
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = 'patch.pd'
    a.click()
    URL.revokeObjectURL(url)
    return true
  } catch {
    return false
  }
}
