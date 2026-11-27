---
id: TASK-5.31
title: Make the durable runner reuse AgentLoop instead of a copy
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
dependencies: []
references:
  - agent/durable.py
  - agent/loop.py
  - tests/test_durable_runs.py
parent_task_id: TASK-5
priority: medium
type: enhancement
ordinal: 41000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
agent/durable.py reimplements the agent loop in `durable_agent_workflow` rather than wrapping agent/loop.py, and the two have drifted. The durable path keeps only the last turn's text, so text written beside `write_patch` is lost; it has no faithfulness check; and it does not track tool errors, recoveries, disallowed calls or blocked injections. Any loop change (for example TASK-5.27's budgets) has to be made twice. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One implementation of the turn logic (text handling, budgets, error and recovery accounting) serves both the streaming and the durable paths
- [ ] #2 A durable run and a streaming run given the same scripted model turns produce the same answer text and the same run metrics
- [ ] #3 Durable runs still checkpoint each model turn and tool call and still resume after a restart, shown by the existing durable-run tests
<!-- AC:END -->
