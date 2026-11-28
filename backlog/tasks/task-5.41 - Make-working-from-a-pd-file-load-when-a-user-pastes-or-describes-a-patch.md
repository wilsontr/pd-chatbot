---
id: TASK-5.41
title: Make working-from-a-pd-file load when a user pastes or describes a patch
status: To Do
assignee: []
created_date: '2026-11-28 02:47'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The skill is almost never selected: load rate 9% (4/45) in CI run 46095333752, against a 50% floor; every pasted-canvas question except pd-file-001 loaded 0/3. Answers are mostly fine without it (faithfulness 4.32, relevance 4.76), so the failure is the description, not the content. Two golden questions are unanswerable in every trial: pd-file-003 (asks to rebuild a subpatch it never shows) and pd-file-013 (refers to a patch 'pasted earlier' with no history), both no_context 3/3, so they count as misses whatever the skill does. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The skill's description makes the model call load_skill for pasted #N canvas text and for questions about reading or changing an existing .pd file
- [ ] #2 pd-file-003 and pd-file-013 are replaced or given the patch text they need, so no golden question is unanswerable by construction
- [ ] #3 The skill passes the CI gate (skills.yml, 3 trials) and its activation carries CI evidence
<!-- AC:END -->
