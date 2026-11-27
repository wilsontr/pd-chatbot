---
id: TASK-5.34
title: Limit concurrent sandbox runs across all server processes
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - sandbox
dependencies: []
references:
  - sandbox/runner.py
  - docs/SANDBOX.md
parent_task_id: TASK-5
priority: low
type: enhancement
ordinal: 44000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`PD_SANDBOX_CONCURRENCY` is enforced by a threading semaphore in sandbox/runner.py, so with several server workers the real limit is workers times the setting, and Modal spend scales with it. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The configured limit holds across all worker processes of one deployment, or the docs state the per-process behaviour and the deployment sets it accordingly
- [ ] #2 docs/SANDBOX.md describes the behaviour
<!-- AC:END -->
