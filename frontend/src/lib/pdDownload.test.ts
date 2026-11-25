import { test } from 'node:test'
import assert from 'node:assert/strict'
import { apiUrl } from './pdDownload.ts'

test('session patch download paths go through the /api rewrite', () => {
  // The backend returns /sessions/...; the browser reaches it through /api.
  assert.equal(
    apiUrl('/sessions/s1/patches/p62eaf970f23.pd'),
    '/api/sessions/s1/patches/p62eaf970f23.pd',
  )
  assert.equal(apiUrl('sessions/s1'), '/api/sessions/s1')
  // Already-prefixed and absolute URLs are left alone.
  assert.equal(apiUrl('/api/sessions/s1'), '/api/sessions/s1')
  assert.equal(apiUrl('https://example.test/x.pd'), 'https://example.test/x.pd')
})
