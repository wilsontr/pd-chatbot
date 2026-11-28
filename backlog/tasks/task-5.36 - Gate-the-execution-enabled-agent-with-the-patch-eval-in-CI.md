---
id: TASK-5.36
title: Gate the execution-enabled agent with the patch eval in CI
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-28 01:26'
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
- [x] #1 A CI job runs `evaluation.patch_eval` with execution on and fails when the with-execution pass rate drops below a documented floor
- [x] #2 Agent-gate thresholds are defined per execution mode and documented next to the gate
- [x] #3 The job's cost per run is stated in the workflow or docs
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. In evaluation/gates.py, define agent thresholds per execution mode (docs = current set, execution = calibrated from docs/PATCH_EVAL.md) and a with-execution pass-rate floor; document them next to the gate.
2. Add --gate/--summary to evaluation.patch_eval: map the with-arm summary to the agent metrics, check the floor and the execution thresholds, exit non-zero on failure.
3. Add .github/workflows/patch-eval.yml: build the sandbox image, build the index, run the with-execution arm gated, state cost per run.
4. Tests for the mapping, the floor and the per-mode thresholds; docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
evaluation/gates.py now holds AGENT_DOCS_THRESHOLDS (the existing set, used by evaluate --gate-agent, which never executes) and AGENT_EXECUTION_THRESHOLDS (pass floor PATCH_EVAL_PASS_FLOOR 0.75 = 12/16, tool calls <= 10, cost <= $0.03, p95 <= 60 s, recovery >= 50%), keyed in AGENT_THRESHOLDS_BY_MODE; AGENT_THRESHOLDS stays as the docs alias. evaluation.patch_eval gained --gate (maps the with-arm summary onto the agent metrics; >25% infrastructure errors fails as unmeasured) and --summary. .github/workflows/patch-eval.yml builds the image and the index and runs '--arm with --gate' on PRs touching agent, sandbox, skills, rag.py, the patch eval or gates.py; cost stated in the workflow, docs/PATCH_EVAL.md and README.
Validation: tests/test_patch_eval.py::TestExecutionGate (a healthy execution run passes these bounds and fails the docs ones; 11/16 fails the floor; runaway tool calls fail; outages fail as unmeasured; recovery and p95 mapping; summary states sample and cost). The exact CI command run locally (2026-11-28): 16/16 passed, 7.1 tool calls, p95 43.9 s, recovery 3/3, $0.0847, gate passed, exit 0. p95 bound raised from 45 s to 60 s after that run, since with one trial the p95 is the slowest answer. Not yet run on GitHub.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The execution-enabled agent is now gated in CI: patch-eval.yml runs the with-execution patch eval in the sandbox on agent, sandbox and skill changes and fails below a 75% pass floor or outside execution-mode agent bounds. Agent thresholds are defined per mode (docs vs execution) next to the gate in evaluation/gates.py; cost is about $0.08 a run. Verified by unit tests and one local run of the CI command (16/16, $0.0847, gate passed).
<!-- SECTION:FINAL_SUMMARY:END -->
