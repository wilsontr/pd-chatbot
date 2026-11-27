---
id: TASK-5.36
title: Gate the execution-enabled agent with the patch eval in CI
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - eval
  - ci
dependencies:
  - TASK-5.35
references:
  - evaluation/patch_eval.py
  - evaluation/gates.py
  - docs/PATCH_EVAL.md
parent_task_id: TASK-5
priority: medium
type: task
ordinal: 46000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The headline patch-eval result (docs/PATCH_EVAL.md: 92% with execution) is produced by hand, and the CI agent eval runs with `PD_SANDBOX_BACKEND` unset, so execution is never measured and recovery is reported as "n/a (0 errors)". The agent gate's thresholds (tool_calls <= 3.5, p95 <= 20 s in evaluation/gates.py) would also fail an execution-enabled run, which measured 6.1 tool calls and 22.5 s p95. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A CI job runs `evaluation.patch_eval` with execution on and fails when the with-execution pass rate drops below a documented floor
- [ ] #2 Agent-gate thresholds are defined per execution mode and documented next to the gate
- [ ] #3 The job's cost per run is stated in the workflow or docs
<!-- AC:END -->
