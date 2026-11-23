---
id: TASK-5.12
title: Keep patches in a per-session workspace for iterative edits
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies:
  - TASK-5.5
  - TASK-5.9
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Patches the agent creates should persist for the session, so follow-ups like "make the filter resonant" edit the existing patch rather than starting over.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Patches created in a session are stored and listed by id
- [ ] #2 The agent can load and modify a stored patch in a follow-up turn
- [ ] #3 Users can download the latest version of each session patch as `.pd`
<!-- AC:END -->
