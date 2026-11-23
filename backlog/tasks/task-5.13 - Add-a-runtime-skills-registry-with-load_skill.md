---
id: TASK-5.13
title: Add a runtime skills registry with load_skill
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies:
  - TASK-5.1
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Support Agent Skills (folders with a `SKILL.md` and optional scripts). The agent sees only each skill's name and description and calls `load_skill(name)` to pull in the full instructions when needed. Skills are versioned and the registry is re-read each turn, so a new skill appears mid-session without a redeploy.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The agent sees skill names and descriptions only until it calls `load_skill`
- [ ] #2 Adding or updating a skill in the watched location takes effect on the next turn without a restart
- [ ] #3 Each run records which skill versions it loaded
- [ ] #4 Tests cover discovery, loading and hot reload
<!-- AC:END -->
