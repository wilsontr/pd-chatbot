---
id: TASK-5.43
title: Ground dsp-debugging answers in their excerpts and make it load for DSP faults
status: To Do
assignee: []
created_date: '2026-11-28 02:48'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fails citation correctness (3.38, floor 4) and load rate (47%) in CI run 46095333752; faithfulness is exactly at the floor (4.00). Citation is low across the slice: dsp-005 (sample-by-sample feedback) 2/2/2, dsp-013 (denormals, which the skill says the docs do not cover) 1/2/2 with faithfulness 2/2/2, dsp-007 and dsp-011 2-3. Questions about block~, throw~, print~ and line~ (dsp-003, 009, 010, 012, 015) never loaded the skill. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 dsp-013-style answers on undocumented topics say plainly that the docs do not cover them and cite nothing they do not support
- [ ] #2 The skill is loaded for at least half of its golden slice
- [ ] #3 The skill passes the CI gate (citation >= 4, faithfulness >= 4) and its activation carries CI evidence
<!-- AC:END -->
