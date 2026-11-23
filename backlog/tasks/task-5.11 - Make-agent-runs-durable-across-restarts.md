---
id: TASK-5.11
title: Make agent runs durable across restarts
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
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
- [ ] #1 A run interrupted by a process restart resumes from its last completed step without repeating finished tool calls
- [ ] #2 A run can pause waiting for an approval and continue once approved
- [ ] #3 The engine choice and trade-offs are recorded as a Backlog decision
<!-- AC:END -->
