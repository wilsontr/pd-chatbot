---
id: TASK-5.15
title: Gate skill activation on per-skill evals
status: To Do
assignee: []
created_date: '2026-11-23 20:41'
labels:
  - agent
dependencies:
  - TASK-5.13
  - TASK-5.7
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Treat a skill as a deployable unit with a quality gate: a skill version is only activated if its own eval slice passes in CI.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 CI runs each changed skill's eval slice
- [ ] #2 A skill version that fails its slice is not activated, and the previous version stays live
- [ ] #3 The eval report shows per-skill results
<!-- AC:END -->
