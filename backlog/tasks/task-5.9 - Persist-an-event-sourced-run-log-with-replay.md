---
id: TASK-5.9
title: Persist an event-sourced run log with replay
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
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
- [ ] #1 Every event the agent loop emits is stored in order with its run id and session id
- [ ] #2 An API endpoint returns the full event history of a run
- [ ] #3 Runs link to their Langfuse trace
- [ ] #4 Tests cover writing and reading a multi-step run
<!-- AC:END -->
