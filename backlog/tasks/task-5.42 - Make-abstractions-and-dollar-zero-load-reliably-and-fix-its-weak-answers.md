---
id: TASK-5.42
title: Make abstractions-and-dollar-zero load reliably and fix its weak answers
status: To Do
assignee: []
created_date: '2026-11-28 02:48'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Load rate 44% in CI run 46095333752 (floor 50%); six questions loaded 0/3 or 1/3, including abs-001 (two copies share a receive value) and abs-007/abs-011/abs-015, which are squarely the skill's topic. Scores otherwise clear the floors (faithfulness 4.53, relevance 4.87, citation 4.29), but abs-010 (editing an abstraction while instances are open) and abs-012 (abstraction not found on the path) score citation 2 in every trial, and abs-004 scores relevance 2-4. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The skill is loaded for at least half of its golden slice in a 3-trial CI run
- [ ] #2 abs-010 and abs-012 answers cite excerpts that support them, or the skill says the docs do not cover the point
- [ ] #3 The skill passes the CI gate and its activation carries CI evidence
<!-- AC:END -->
