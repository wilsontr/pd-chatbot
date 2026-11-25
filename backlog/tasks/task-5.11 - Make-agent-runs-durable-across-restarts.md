---
id: TASK-5.11
title: Make agent runs durable across restarts
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 21:02'
labels:
  - agent
dependencies:
  - TASK-5.9
parent_task_id: TASK-5
priority: low
type: feature
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Wrap the agent loop in a durable-execution engine (DBOS, Temporal or Inngest; DBOS is the lightest fit for Railway plus Postgres) so long sandbox runs survive a redeploy and can pause for human approval.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A run interrupted by a process restart resumes from its last completed step without repeating finished tool calls
- [x] #2 A run can pause waiting for an approval and continue once approved
- [x] #3 The engine choice and trade-offs are recorded as a Backlog decision
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Record Backlog decision comparing DBOS, Temporal, and Inngest, selecting DBOS Transact (dbos) as the Postgres-native, library-only durable execution engine for Railway + SQLite testing.
2. Add dbos to requirements.txt and install in venv; create agent/durable.py with DBOS lifecycle helpers (configure/launch/destroy targeting DBOS_SYSTEM_DATABASE_URL / DATABASE_URL / local SQLite) and durable workflow orchestration keyed by workflow_id = run_id.
3. Wrap each model turn and each tool execution in @DBOS.step() inside an async @DBOS.workflow(), persisting events to RunStore and checkpointing PatchWorkspace state between steps so process restarts resume from the last completed step without repeating finished tool calls or model turns.
4. Add human-in-the-loop approval pause/resume using DBOS.recv_async / DBOS.send_async (and ApprovalRequested / ApprovalResolved events in agent/events.py + RunStore status 'waiting_approval'), integrated with ToolPolicy.require_approval (e.g., Slack sandbox tools).
5. Expose POST /runs/durable and POST /runs/{run_id}/approve in main.py (with DBOS startup/shutdown lifecycle) and add tests in tests/test_durable_runs.py proving (a) true cross-process restart recovery without repeating finished tool calls and (b) pausing on an approval-gated tool and resuming to completion once approved (plus denial/timeout handling).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented DBOS-backed durable agent runs:
- Architecture decision: recorded decision-1 (backlog/decisions/decision-1 - Use-DBOS-Transact-for-durable-agent-runs.md) comparing DBOS Transact vs Temporal vs Inngest and documenting trade-offs.
- Durable engine (agent/durable.py, requirements.txt, .gitignore, agent/__init__.py): added dbos==3.7.0, DBOS lifecycle helpers (launch_dbos, destroy_dbos, dbos_database_url targeting DBOS_SYSTEM_DATABASE_URL / DATABASE_URL / local SQLite), DurableRuntime factory container, and durable_agent_workflow keyed by workflow_id = run_id. Each model turn, tool execution, approval transition, and RunStore write is wrapped in @DBOS.step() with idempotent RunStore persistence (ignore_existing=True in agent/store.py) and JSON-serializable PatchWorkspace snapshots (to_snapshot/from_snapshot in agent/workspace.py).
- Human approval (agent/events.py, agent/store.py, agent/durable.py, main.py): added ApprovalRequested and ApprovalResolved events, RunStore.update_run_status(), durable pause/resume via DBOS.recv_async / DBOS.send_async integrated with ToolPolicy.require_approval, and HTTP endpoints POST /runs/durable (202) and POST /runs/{run_id}/approve (plus pending_approval in GET /runs/{run_id}).
- Validation: ./venv/bin/pytest tests/test_durable_runs.py -v (8 passed — including cross-process os._exit(42) crash/restart recovery without repeating finished tool calls, Slack pause/approve/resume, denial, timeout, and HTTP API tests) and ./venv/bin/pytest -m 'not slow' -q (629 passed, 81 skipped, 4 deselected).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Wrapped the agent loop in DBOS Transact (agent/durable.py) with @DBOS.step() checkpoints for model turns, tool calls, and PatchWorkspace snapshots, human-in-the-loop pause/resume via DBOS.recv_async/send_async and POST /runs/durable + POST /runs/{run_id}/approve in main.py, and recorded the DBOS vs Temporal vs Inngest decision as decision-1. Verified with pytest tests/test_durable_runs.py (8 passed, including real subprocess crash/restart recovery without repeating finished tools) and pytest -m 'not slow' (629 passed, 81 skipped).
<!-- SECTION:FINAL_SUMMARY:END -->
