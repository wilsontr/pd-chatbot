---
id: TASK-5.35
title: Run the sandbox-dependent tests in CI
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
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
- [ ] #1 CI builds the sandbox image and runs every `needs_sandbox` test on pull requests that touch the agent, the patch tools, the sandbox or the skills
- [ ] #2 Those tests fail rather than skip in that job when the image is missing (PD_SANDBOX_REQUIRED)
<!-- AC:END -->
