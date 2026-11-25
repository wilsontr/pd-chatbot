import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { applyStreamEvent, finalizeRunningSteps } from './chatStream.ts'
import type { Message, PdPatch } from '../types.ts'

const SAMPLE_PATCH: PdPatch = {
  objects: [
    { id: '0', type: 'obj', text: 'osc~ 440', inlets: 2, outlets: 1 },
    { id: '1', type: 'obj', text: 'dac~', inlets: 2, outlets: 0 },
  ],
  connections: [{ srcId: '0', srcOutlet: 0, dstId: '1', dstInlet: 0 }],
}

describe('applyStreamEvent', () => {
  it('leaves steps and audio undefined for answers without tool use', () => {
    let msg: Message = { role: 'assistant', content: '' }
    msg = applyStreamEvent(msg, {
      type: 'meta',
      sources: [
        {
          heading_path: 'osc~',
          url: 'https://example.test/osc',
          source: 'iem_reference',
          content_type: 'object_reference',
          object_name: 'osc~',
        },
      ],
    })
    msg = applyStreamEvent(msg, { type: 'chunk', text: 'osc~ is a cosine oscillator.' })
    msg = applyStreamEvent(msg, { type: 'done', history: [], message_id: 'm1' })

    assert.equal(msg.content, 'osc~ is a cosine oscillator.')
    assert.equal(msg.messageId, 'm1')
    assert.equal(msg.steps, undefined)
    assert.equal(msg.audio, undefined)
    assert.equal(msg.patches, undefined)
    assert.equal(msg.sources?.length, 1)
  })

  it('tracks streaming tool calls before meta, text_reset, patches, and audio', () => {
    let msg: Message = { role: 'assistant', content: '' }

    msg = applyStreamEvent(msg, {
      type: 'tool_call',
      id: 'c1',
      name: 'search_docs',
      arguments: { query: 'osc~' },
    })
    assert.equal(msg.steps?.length, 1)
    assert.equal(msg.steps?.[0].status, 'running')

    msg = applyStreamEvent(msg, {
      type: 'tool_result',
      id: 'c1',
      name: 'search_docs',
      ok: true,
      summary: "1 section for 'osc~'",
      chunk_count: 1,
      data: { headings: ['osc~'] },
    })
    assert.equal(msg.steps?.[0].status, 'done')
    assert.equal(msg.steps?.[0].summary, "1 section for 'osc~'")
    assert.equal(msg.steps?.[0].chunkCount, 1)

    msg = applyStreamEvent(msg, { type: 'meta', sources: [] })
    msg = applyStreamEvent(msg, { type: 'chunk', text: 'Let me build that.' })
    msg = applyStreamEvent(msg, { type: 'text_reset' })
    assert.equal(msg.content, '')

    msg = applyStreamEvent(msg, {
      type: 'tool_call',
      id: 'c2',
      name: 'run_patch',
      arguments: { name: 'sine', seconds: 2 },
    })
    msg = applyStreamEvent(msg, {
      type: 'tool_result',
      id: 'c2',
      name: 'run_patch',
      ok: false,
      summary: 'sandbox unavailable',
    })
    assert.equal(msg.steps?.[1].status, 'error')

    msg = applyStreamEvent(msg, { type: 'chunk', text: 'Here is a 440 Hz patch.' })
    msg = applyStreamEvent(msg, {
      type: 'artifact',
      kind: 'pd_patch',
      data: { name: 'sine', patch: SAMPLE_PATCH },
    })
    msg = applyStreamEvent(msg, {
      type: 'artifact',
      kind: 'audio',
      data: {
        id: 'a1',
        url: '/artifacts/a1',
        name: 'sine',
        mime: 'audio/wav',
        seconds: 2,
      },
    })
    msg = applyStreamEvent(msg, { type: 'done', history: [], message_id: 'm2' })

    assert.equal(msg.content, 'Here is a 440 Hz patch.')
    assert.equal(msg.patches?.length, 1)
    assert.equal(msg.audio?.length, 1)
    assert.equal(msg.audio?.[0].name, 'sine')
    assert.equal(msg.messageId, 'm2')
  })

  it('finalizes dangling running steps without mutating undefined steps', () => {
    assert.equal(finalizeRunningSteps(undefined, 'error'), undefined)
    const done = finalizeRunningSteps(
      [{ id: '1', name: 'run_patch', arguments: {}, status: 'running' }],
      'error',
    )
    assert.equal(done?.[0].status, 'error')
  })
})
