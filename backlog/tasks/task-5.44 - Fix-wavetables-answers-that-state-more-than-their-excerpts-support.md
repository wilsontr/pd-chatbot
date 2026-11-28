---
id: TASK-5.44
title: Fix wavetables answers that state more than their excerpts support
status: To Do
assignee: []
created_date: '2026-11-28 02:48'
labels:
  - agent
  - skills
  - wavetables
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fails faithfulness (3.82, floor 4) in CI run 46095333752. The low scores come from the routes the skill prescribes: wt-003 (sinesum partials) 3/3/3, wt-005 (computed shape via the until loop) 3/3/3, wt-013 (sine without sinesum) 3/2/3, wt-015 (reading the table back) 2/3/2 - the skill's own constructions (the fill loop, the read-back check, the message syntax) are presented as documented. wt-007/008/009/010/014 never loaded the skill. Deactivating it is not an option to reach for: it is what took the p_14 patch task from 1/6 to 6/6 (TASK-5.28, docs/PATCH_EVAL.md). Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Answers that use the fill loop, the read-back check or message syntax not shown in an excerpt flag them as the assistant's construction
- [ ] #2 p_14 in the with-execution patch eval still passes (3 trials)
- [ ] #3 The skill passes the CI gate (faithfulness >= 4) and its activation carries CI evidence
<!-- AC:END -->
