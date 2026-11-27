---
id: TASK-5.39
title: Turn on patch execution in production on Modal
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - sandbox
  - deploy
dependencies: []
documentation:
  - docs/SANDBOX.md
parent_task_id: TASK-5
priority: medium
type: task
ordinal: 49000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.26 decided to host the sandbox on Modal and listed the steps needed before production (docs/SANDBOX.md): set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET on the Railway service, set a Modal spending limit, and set PD_SANDBOX_BACKEND=modal. No task tracks those steps, and unless the backend is set the deployed app does not run patches at all. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The production service runs patches on Modal, shown by a successful run_patch in a production request
- [ ] #2 A spending limit is set on the Modal workspace
- [ ] #3 docs/SANDBOX.md records the production setting and how to turn it off
<!-- AC:END -->
