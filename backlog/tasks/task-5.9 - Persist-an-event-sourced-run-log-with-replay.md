---
id: TASK-5.9
title: Persist an event-sourced run log with replay
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 18:26'
labels:
  - agent
dependencies:
  - TASK-5.1
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Store every model turn, tool call and tool result in Postgres keyed by run id, so runs can be replayed, debugged and resumed. Link runs to their Langfuse traces.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every event the agent loop emits is stored in order with its run id and session id
- [x] #2 An API endpoint returns the full event history of a run
- [x] #3 Runs link to their Langfuse trace
- [x] #4 Tests cover writing and reading a multi-step run
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extend agent/events.py and agent/loop.py to emit ModelTurnEvent (tokens, cost, latency, content blocks) and record ToolResult latency and Done run/session/trace ids.
2. Add trace_id, trace_url, and trace_context helpers in agent/observability.py to link runs and Langfuse traces bidirectionally.
3. Implement agent/store.py with SQLAlchemy Core tables (runs, run_events) for Postgres/SQLite, RunStore CRUD/replay/state-reconstruction methods, and RunRecorder for ordered non-blocking persistence.
4. Wire run_id, session_id, langfuse trace links, and RunStore persistence into agent/chat.py and expose GET /runs/{run_id} (and GET /runs) in main.py.
5. Add unit and API tests covering multi-step runs, ordering, session/run ids, replay, Langfuse linking, and update existing event-sequence tests.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented event-sourced run log with replay and Langfuse trace linking:
- Events & timing (agent/events.py, agent/loop.py, agent/__init__.py): added ModelTurnEvent (turn, model, raw content blocks, text, stop_reason, usage, cost_usd, latency_ms), ToolResult.latency_ms, Done run_id/session_id/langfuse_trace_id/langfuse_trace_url fields, and to_log_dict() for lossless persistence of tool outputs and retrieved chunks.
- Langfuse linking (agent/observability.py, agent/loop.py, agent/chat.py): added trace_id(), trace_url(), and trace_context() helpers; root chat span metadata records run_id and session_id, child agent_turn spans attach via trace_context, and runs/Done store langfuse_trace_id and langfuse_trace_url.
- Persistence & replay (agent/store.py): added SQLAlchemy Core schema (runs and run_events tables with (run_id, seq) uniqueness and session_id index) supporting Postgres via DATABASE_URL (psycopg 3) and SQLite locally (sqlite:///runs.db). Added RunStore (start_run, append_event/s, finish_run, get_run, get_events, list_runs, replay_events, reconstruct_state) and RunRecorder (immediate structural-event flushes with batched text_delta flushes and non-fatal error handling).
- Orchestration & API (agent/chat.py, main.py): wired run_id, session_id, and run_store into stream_chat across all paths (normal, refusal, cache hit, error); added optional session_id to ChatRequest and exposed API-key-protected GET /runs/{run_id} and GET /runs endpoints.
- Validation: ./venv/bin/pytest tests/test_run_store.py -v (11 passed) and ./venv/bin/pytest -m 'not slow' -q (621 passed, 81 skipped, 4 deselected).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented Postgres/SQLite event-sourced run logging (agent/store.py) storing every agent event (including new ModelTurnEvent with per-turn tokens, cost, and latency) in strict seq order keyed by run_id and session_id, with typed replay and state reconstruction, bidirectional Langfuse trace linking, and GET /runs/{run_id} + GET /runs API endpoints in main.py. Verified with pytest tests/test_run_store.py (11 passed) and pytest -m 'not slow' (621 passed, 81 skipped).
<!-- SECTION:FINAL_SUMMARY:END -->
