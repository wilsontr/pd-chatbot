---
id: TASK-5.45
title: Lift granular-synthesis clear of the citation floor
status: To Do
assignee: []
created_date: '2026-11-28 02:48'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: low
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
granular-synthesis sits on the gate line: two CI runs of the same version gave citation 4.06 (pass, run 46095333752; the activation was lost to a rejected push since fixed in b025b94) and 3.98 (fail, run 46098822877), with load rate 56% and 52%. Low citations cluster on gs-007 (time-stretch) 2/3/2 and gs-011 (Doppler from a moving read position) 2/3/2; six questions (gs-005/006/007/009/015/016) never load the skill. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Two consecutive 3-trial CI runs both pass the gate
- [ ] #2 gs-007 and gs-011 answers cite excerpts that support their claims
- [ ] #3 The activation carries CI evidence
<!-- AC:END -->
