---
id: TASK-5.5
title: Give the agent patch execution and audio analysis tools
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies:
  - TASK-5.1
  - TASK-5.3
  - TASK-5.4
parent_task_id: TASK-5
priority: high
type: feature
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add `write_patch`, `run_patch` and `analyze_audio` tools so the agent can build a patch, run it in the sandbox, inspect the result (RMS, peak frequency, spectrum summary, clipping/silence) and fix it. Making patches a tool call also replaces the current fenced `pd-patch` block, which is coaxed out by prompt text and parsed afterwards, with structured tool input.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Patches are produced through a tool call with a JSON schema; fenced `pd-patch` parsing is no longer needed
- [ ] #2 The agent can run a patch, read errors and audio analysis, and revise the patch within one request
- [ ] #3 An eval metric reports the share of answers whose patches run cleanly and meet the question's spec, with and without execution tools
- [ ] #4 Rendered audio is returned as an artifact event the interfaces can play
<!-- AC:END -->
