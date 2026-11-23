---
id: TASK-5.17
title: Add a pdbot CLI
status: To Do
assignee: []
created_date: '2026-11-23 20:41'
labels:
  - agent
dependencies:
  - TASK-5.1
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A terminal interface over the agent core: ask questions with streamed output, run local `.pd` files in the sandbox, and replay stored runs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `pdbot ask` streams an answer with tool steps shown
- [ ] #2 Patches from an answer can be written to disk as `.pd` files
- [ ] #3 `pdbot run <file.pd>` and `pdbot replay <run_id>` work once their dependencies exist
- [ ] #4 The CLI is installable via `pyproject.toml` and documented in the README
<!-- AC:END -->
