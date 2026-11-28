---
id: TASK-5.35
title: Run the sandbox-dependent tests in CI
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-28 01:23'
labels:
  - agent
  - ci
  - sandbox
dependencies: []
references:
  - .github/workflows/eval.yml
  - .github/workflows/sandbox.yml
  - tests/test_patch_eval.py
parent_task_id: TASK-5
priority: medium
type: task
ordinal: 45000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
eval.yml never builds the pd-sandbox image, so every test marked `needs_sandbox` in tests/test_patch_tools.py, tests/test_patch_eval.py and tests/test_wavetables.py is skipped in CI. sandbox.yml builds the image but runs only tests/test_sandbox.py, and only when sandbox/** changes. The agent's patch-execution behaviour therefore has no CI coverage. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CI builds the sandbox image and runs every `needs_sandbox` test on pull requests that touch the agent, the patch tools, the sandbox or the skills
- [x] #2 Those tests fail rather than skip in that job when the image is missing (PD_SANDBOX_REQUIRED)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Move the sandbox skip into one shared marker (tests/needs_sandbox.py) that also marks tests 'sandbox'; switch test_sandbox, test_patch_tools, test_patch_eval and test_wavetables to it.
2. Register the marker in pyproject.toml.
3. Rewrite sandbox.yml: build the image and run 'pytest -m sandbox' with PD_SANDBOX_REQUIRED=1 on PRs and pushes touching agent, patch tools, sandbox, skills or the patch eval.
4. Add a test that pins the workflow trigger and that no test file keeps its own image check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Added tests/needs_sandbox.py: one decorator that marks a test 'sandbox' and applies the image skip (bypassed by PD_SANDBOX_REQUIRED). test_sandbox, test_patch_tools, test_patch_eval and test_wavetables use it; the marker is registered in pyproject.toml. sandbox.yml now installs requirements, builds the image and runs 'python -m pytest -m sandbox' with PD_SANDBOX_REQUIRED=1 on PRs and pushes touching agent/**, sandbox/**, skills/**, the patch eval, pd_schema.py, the four test files or requirements.txt (timeout 30 min, ~70 tests each starting a container).
Validation: 'pytest -m sandbox' collects 70 tests across the four files; run locally with the image: 67 passed, 3 failed + 1 teardown error from Docker Desktop wall-clock timeouts under a 12-minute run (p_05, p_13, test_parallel_runs), all three pass when rerun alone. With PD_SANDBOX_IMAGE pointing at a missing image: 6/6 wavetable sandbox tests fail with PD_SANDBOX_REQUIRED=1 and skip without it. tests/test_ci_workflows.py pins the trigger paths, -m sandbox, PD_SANDBOX_REQUIRED and that no test file keeps its own image check. The workflow has not run on GitHub yet.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every test that needs the pd-sandbox image now carries one shared marker (tests/needs_sandbox.py), and sandbox.yml builds the image and runs 'pytest -m sandbox' with PD_SANDBOX_REQUIRED=1 whenever the agent, patch tools, sandbox, skills or patch eval change, so a missing image fails instead of skipping. Verified locally (70 collected; required-and-missing fails, otherwise skips) and pinned by tests/test_ci_workflows.py; first GitHub run pending.
<!-- SECTION:FINAL_SUMMARY:END -->
