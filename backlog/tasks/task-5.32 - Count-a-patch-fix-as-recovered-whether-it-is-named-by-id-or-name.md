---
id: TASK-5.32
title: Count a patch fix as recovered whether it is named by id or name
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - eval
dependencies: []
references:
  - agent/loop.py
  - agent/workspace.py
parent_task_id: TASK-5
priority: low
type: bug
ordinal: 42000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`_tool_error_key` in agent/loop.py identifies a patch only by the `name` argument. When the model refers to a stored patch by `id` (TASK-5.12), a failed `run_patch` and the later clean run get different keys, so the recovery is not counted and the error stays unresolved. This skews the recovery metric that the agent gate checks. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A failed run and a later clean run of the same patch count as one recovery whether each call used the id or the name
- [ ] #2 Tests cover the id/name mixes
<!-- AC:END -->
