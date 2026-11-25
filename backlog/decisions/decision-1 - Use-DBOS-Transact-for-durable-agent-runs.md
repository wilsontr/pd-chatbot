---
id: decision-1
title: Use DBOS Transact for durable agent runs
date: '2026-11-25 20:53'
status: accepted
---
## Context

TASK-5.11 requires wrapping the pd-chatbot agent loop in a durable-execution engine so long sandbox runs survive Railway redeploys/process restarts without repeating finished tool calls, and so runs can pause indefinitely waiting for human approval (for example, Slack approval before executing headless Pure Data patches).

The application deploys as a single FastAPI service on Railway with an attached PostgreSQL database (`DATABASE_URL`), and uses SQLite for local development and offline pytest runs.

We evaluated three durable-execution engines:

1. **DBOS Transact (`dbos`)**: An open-source (Apache 2.0) Python library that checkpoints workflows and steps directly into Postgres (or SQLite locally) inside the existing application process.
2. **Temporal**: A battle-tested distributed workflow platform with deterministic replay, strong versioning, and a dedicated web UI, backed by a Temporal Server cluster or Temporal Cloud plus separate worker processes.
3. **Inngest**: An event-driven durable workflow platform with built-in `step.waitForEvent`, backed by Inngest Cloud (external SaaS webhooks calling back into Railway) or a self-hosted Inngest server.

## Decision

Adopt **DBOS Transact (`dbos`)** as the durable-execution engine for pd-chatbot (`agent/durable.py`):

- **Zero extra infrastructure**: DBOS runs in-process inside FastAPI and persists checkpoints in the existing Railway Postgres database (`DBOS_SYSTEM_DATABASE_URL` / `DATABASE_URL`) and in local SQLite during development and pytest.
- **Native step checkpointing**: Each model turn and each tool execution is wrapped in `@DBOS.step()` inside an `@DBOS.workflow()` keyed by `workflow_id = run_id` (`SetWorkflowID`). On process restart, `DBOS.launch()` automatically recovers pending workflows from their last completed step without repeating finished tool calls or LLM turns.
- **Built-in human-in-the-loop pause/resume**: Approval-gated tools (declared via `ToolPolicy.require_approval`) pause durably via `DBOS.recv_async(topic=...)` and resume via `DBOS.send_async(destination_id=run_id, ...)`, surfaced through `POST /runs/{run_id}/approve` and `RunStore` status `"waiting_approval"`.
- **Single-ID observability**: Setting DBOS `workflow_id` equal to `RunStore`'s `run_id` lets durable runs appear in `GET /runs/{run_id}` and the `/runs/:runId` timeline replay UI alongside non-durable runs.

### Why not Temporal or Inngest

- **Temporal** was rejected because running and operating a Temporal Server cluster (or paying for Temporal Cloud) plus separate worker processes is disproportionate operational overhead for a single-service portfolio app.
- **Inngest** was rejected because it depends on external SaaS webhook callbacks (or another self-hosted server), introduces network latency on every step boundary, and complicates hermetic local/CI testing.

## Consequences

### Positive
- No new managed services, sidecars, or queues to provision or pay for on Railway.
- Hermetic, fast pytest verification using SQLite (`DBOS.destroy()`), including real subprocess crash/restart recovery tests.
- Clean reuse of existing `ToolPolicy.require_approval`, `RunStore`, and `/runs/:runId` replay UI.

### Trade-offs & Constraints
- **Serializable step boundaries**: Workflow inputs and step return values must be JSON/pickle-serializable, so live objects (`ModelClient`, `ToolRegistry`, `PatchWorkspace`, `RunStore`) cannot be passed as workflow parameters; `agent/durable.py` resolves runtime dependencies via a process-local runtime factory and checkpoints `PatchWorkspace` snapshots across steps.
- **Process-local singleton**: DBOS operates as a process-wide singleton (`DBOS(...)` / `DBOS.launch()` / `DBOS.destroy()`), requiring explicit lifecycle hooks in FastAPI and clean teardown between tests.
- **Multi-replica scaling**: If pd-chatbot later scales beyond a single Railway replica, each replica will need a distinct `executor_id` (or DBOS Conductor) to coordinate recovery across instances.


