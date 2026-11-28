---
id: TASK-5.46
title: Fix pd-flavors relevance so its grown golden set can be activated
status: To Do
assignee: []
created_date: '2026-11-28 02:48'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: low
ordinal: 57000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The version with the 15-question golden set fails relevance (4.38, floor 4.5) in CI run 46095333752; the earlier CI-gated version keeps serving, so main's skills check passes for this skill. Relevance drops on questions about forks the docs do not cover: flavor-010 (Purr Data preferences) 2/3/2, flavor-009 (Pd-L2Ork vs Purr Data) 3/4/3, flavor-001 3/3/4 - the skill's hedging ('the docs cover Vanilla') crowds out a useful answer. flavor-011 ([mtof~]) never loads and cites 2/2/2. flavor-005 returns no context in 2 of 3 trials. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Answers about uncovered forks still give the user something actionable (where to look, what to check) alongside the caveat
- [ ] #2 No golden question returns no context in most trials
- [ ] #3 The new version passes the CI gate and its activation carries CI evidence
<!-- AC:END -->
