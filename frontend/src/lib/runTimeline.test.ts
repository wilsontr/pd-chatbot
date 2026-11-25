import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  buildTimelineSteps,
  cumulativeMetrics,
  formatCost,
  formatLatency,
  formatTokens,
} from './runTimeline.ts'
import { parseRoute, runPath, runsPath } from './routes.ts'
import type { RunDetail } from '../types.ts'

const MULTI_STEP_RUN: RunDetail = {
  run_id: 'run-demo-1',
  session_id: 'sess-demo-1',
  question: 'Build a 440 Hz sine patch',
  interface: 'web',
  status: 'completed',
  query_type: 'object_reference',
  answer: 'Here is a 440 Hz sine patch.',
  model: 'claude-haiku-4-5',
  turns: 3,
  tool_calls: 2,
  total_tokens: 1420,
  cost_usd: 0.00185,
  latency_ms: 1450.5,
  stop_reason: 'end_turn',
  budget_exceeded: null,
  refused: false,
  cached: false,
  langfuse_trace_id: 'trace-abc123',
  langfuse_trace_url: 'https://cloud.langfuse.com/project/p1/traces/trace-abc123',
  created_at: '2026-11-25T18:00:00.000Z',
  completed_at: '2026-11-25T18:00:01.450Z',
  events: [
    {
      seq: 0,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'model_turn',
      created_at: '2026-11-25T18:00:00.320Z',
      payload: {
        type: 'model_turn',
        turn: 1,
        model: 'claude-haiku-4-5',
        content: [
          { type: 'tool_use', id: 'c1', name: 'search_docs', input: { query: 'osc~' } },
        ],
        text: '',
        stop_reason: 'tool_use',
        usage: { input: 200, output: 50, cache_read: 0, cache_write: 0, total: 250 },
        cost_usd: 0.0004,
        latency_ms: 320,
      },
    },
    {
      seq: 1,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'tool_call',
      created_at: '2026-11-25T18:00:00.321Z',
      payload: {
        type: 'tool_call',
        id: 'c1',
        name: 'search_docs',
        arguments: { query: 'osc~' },
      },
    },
    {
      seq: 2,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'tool_result',
      created_at: '2026-11-25T18:00:00.375Z',
      payload: {
        type: 'tool_result',
        id: 'c1',
        name: 'search_docs',
        ok: true,
        summary: "1 section for 'osc~'",
        content: 'osc~ is a cosine oscillator.',
        chunks: [{ id: 'p1', heading_path: 'osc~' }],
        data: { matched: 1 },
        latency_ms: 54,
      },
    },
    {
      seq: 3,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'sources',
      created_at: '2026-11-25T18:00:00.376Z',
      payload: {
        type: 'sources',
        query_type: 'object_reference',
        sources: [
          {
            heading_path: 'osc~',
            url: 'https://example.test/osc~',
            source: 'iem_reference',
            content_type: 'object_reference',
            object_name: 'osc~',
          },
        ],
      },
    },
    {
      seq: 4,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'model_turn',
      created_at: '2026-11-25T18:00:00.810Z',
      payload: {
        type: 'model_turn',
        turn: 2,
        model: 'claude-haiku-4-5',
        content: [{ type: 'tool_use', id: 'c2', name: 'write_patch', input: { name: 'sine' } }],
        text: '',
        stop_reason: 'tool_use',
        usage: { input: 450, output: 90, cache_read: 0, cache_write: 0, total: 540 },
        cost_usd: 0.00075,
        latency_ms: 434,
      },
    },
    {
      seq: 5,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'tool_call',
      created_at: '2026-11-25T18:00:00.811Z',
      payload: {
        type: 'tool_call',
        id: 'c2',
        name: 'write_patch',
        arguments: { name: 'sine', objects: [], connections: [] },
      },
    },
    {
      seq: 6,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'tool_result',
      created_at: '2026-11-25T18:00:00.820Z',
      payload: {
        type: 'tool_result',
        id: 'c2',
        name: 'write_patch',
        ok: true,
        summary: 'wrote sine (2 boxes)',
        content: 'saved sine',
        chunks: [],
        data: { name: 'sine', revision: 1 },
        latency_ms: 9,
      },
    },
    {
      seq: 7,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'text_delta',
      created_at: '2026-11-25T18:00:01.000Z',
      payload: { type: 'text_delta', text: 'Here is a ' },
    },
    {
      seq: 8,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'text_delta',
      created_at: '2026-11-25T18:00:01.180Z',
      payload: { type: 'text_delta', text: '440 Hz sine patch.' },
    },
    {
      seq: 9,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'model_turn',
      created_at: '2026-11-25T18:00:01.200Z',
      payload: {
        type: 'model_turn',
        turn: 3,
        model: 'claude-haiku-4-5',
        content: [{ type: 'text', text: 'Here is a 440 Hz sine patch.' }],
        text: 'Here is a 440 Hz sine patch.',
        stop_reason: 'end_turn',
        usage: { input: 600, output: 30, cache_read: 0, cache_write: 0, total: 630 },
        cost_usd: 0.0007,
        latency_ms: 380,
      },
    },
    {
      seq: 10,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'artifact',
      created_at: '2026-11-25T18:00:01.210Z',
      payload: {
        type: 'artifact',
        kind: 'pd_patch',
        data: {
          name: 'sine',
          patch: {
            objects: [{ id: '0', type: 'obj', text: 'osc~ 440', inlets: 2, outlets: 1 }],
            connections: [],
          },
        },
      },
    },
    {
      seq: 11,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'artifact',
      created_at: '2026-11-25T18:00:01.215Z',
      payload: {
        type: 'artifact',
        kind: 'audio',
        data: {
          id: 'aud-1',
          url: '/artifacts/aud-1',
          name: 'sine',
          mime: 'audio/wav',
          seconds: 2.0,
        },
      },
    },
    {
      seq: 12,
      run_id: 'run-demo-1',
      session_id: 'sess-demo-1',
      event_type: 'done',
      created_at: '2026-11-25T18:00:01.450Z',
      payload: {
        type: 'done',
        answer: 'Here is a 440 Hz sine patch.',
        turns: 3,
        tool_calls: 2,
        refused: false,
        cached: false,
        patch_failed: false,
      },
    },
  ],
}

describe('buildTimelineSteps', () => {
  it('preserves model turns, tool calls, results, text output, artifacts, and done in order', () => {
    const steps = buildTimelineSteps(MULTI_STEP_RUN)

    assert.deepEqual(
      steps.map(s => s.kind),
      [
        'model_turn',
        'tool_call',
        'tool_result',
        'sources',
        'model_turn',
        'tool_call',
        'tool_result',
        'text_output',
        'model_turn',
        'artifact',
        'artifact',
        'done',
      ],
    )

    // First model turn carries its exact tokens, cost, and latency.
    assert.equal(steps[0].tokens, 250)
    assert.equal(steps[0].costUsd, 0.0004)
    assert.equal(steps[0].latencyMs, 320)
    assert.equal(steps[0].offsetMs, 320)

    // Tool result carries its exact latency and full content/chunks.
    assert.equal(steps[2].latencyMs, 54)
    assert.equal(steps[2].toolContent, 'osc~ is a cosine oscillator.')
    assert.equal(steps[2].toolChunks?.length, 1)

    // Consecutive text_deltas collapse into one text_output step keeping seq span & duration.
    const textStep = steps[7]
    assert.equal(textStep.text, 'Here is a 440 Hz sine patch.')
    assert.equal(textStep.deltaCount, 2)
    assert.equal(textStep.seqStart, 7)
    assert.equal(textStep.seqEnd, 8)
    assert.equal(textStep.latencyMs, 180)

    // Artifacts preserve typed patch & audio payloads in order.
    assert.equal(steps[9].patchArtifact?.name, 'sine')
    assert.equal(steps[10].audioArtifact?.id, 'aud-1')

    // Terminal done step reflects total run tokens, cost, and latency.
    const doneStep = steps[11]
    assert.equal(doneStep.tokens, 1420)
    assert.equal(doneStep.costUsd, 0.00185)
    assert.equal(doneStep.latencyMs, 1450.5)
  })

  it('computes cumulative metrics progressively without double-counting done', () => {
    const steps = buildTimelineSteps(MULTI_STEP_RUN)

    const afterTurn1 = cumulativeMetrics(steps, 3)
    assert.equal(afterTurn1.tokens, 250)
    assert.equal(afterTurn1.costUsd, 0.0004)
    assert.equal(afterTurn1.latencyMs, 374) // 320 + 0 + 54
    assert.equal(afterTurn1.modelTurns, 1)
    assert.equal(afterTurn1.toolCalls, 1)

    const all = cumulativeMetrics(steps, steps.length)
    assert.equal(all.tokens, 250 + 540 + 630)
    assert.equal(all.costUsd, 0.00185)
    assert.equal(all.modelTurns, 3)
    assert.equal(all.toolCalls, 2)
    assert.equal(all.artifacts, 2)
  })
})

describe('formatting and routing helpers', () => {
  it('formats tokens, cost, and latency cleanly', () => {
    assert.equal(formatTokens(1420), '1,420 tok')
    assert.equal(formatTokens(0), '0 tok')
    assert.equal(formatCost(0), '$0.00')
    assert.equal(formatCost(0.00005), '$0.000050')
    assert.equal(formatCost(0.00185), '$0.0019')
    assert.equal(formatCost(0.025), '$0.025')
    assert.equal(formatLatency(54), '54 ms')
    assert.equal(formatLatency(1450), '1.45 s')
  })

  it('parses chat, run list, and run detail routes', () => {
    assert.deepEqual(parseRoute('/'), { view: 'chat' })
    assert.deepEqual(parseRoute('/runs'), { view: 'runs', sessionId: null })
    assert.deepEqual(parseRoute('/runs/', '?session_id=s-1'), {
      view: 'runs',
      sessionId: 's-1',
    })
    assert.deepEqual(parseRoute('/runs/run-abc'), { view: 'run', runId: 'run-abc' })
    assert.equal(runPath('run-abc'), '/runs/run-abc')
    assert.equal(runsPath('s-1'), '/runs?session_id=s-1')
  })
})
