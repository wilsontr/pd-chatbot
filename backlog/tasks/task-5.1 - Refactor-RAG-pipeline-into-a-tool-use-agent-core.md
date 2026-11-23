---
id: TASK-5.1
title: Refactor RAG pipeline into a tool-use agent core
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies: []
references:
  - rag.py
  - main.py
parent_task_id: TASK-5
priority: high
type: enhancement
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Move the request logic out of `rag.py`/`main.py` into a transport-independent agent package. Retrieval becomes a tool the model calls, rather than a fixed step. The loop emits a typed event stream (text deltas, tool calls, tool results, artifacts) so web, CLI, Slack and MCP can all be thin adapters.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Retrieval (hybrid search plus parent lookup) is exposed as a tool the model chooses to call
- [ ] #2 The agent loop yields typed events and has no FastAPI or SSE imports
- [ ] #3 The existing web chat works end to end on the new core, including streaming, caching, guardrails and patch validation
- [ ] #4 The existing retrieval and generation evals run against the agent and the results are compared with the pipeline in the eval report
- [ ] #5 Unit tests cover the loop with a stubbed model client
<!-- AC:END -->
