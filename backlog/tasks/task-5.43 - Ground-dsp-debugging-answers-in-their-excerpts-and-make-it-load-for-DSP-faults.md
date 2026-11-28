---
id: TASK-5.43
title: Ground dsp-debugging answers in their excerpts and make it load for DSP faults
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-28 02:48'
updated_date: '2026-11-28 07:23'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Trigger-style description listing the symptoms it covers.
2. Make the documented anchors search targets: cite only the Source URL of an excerpt retrieved in this answer; denormal advice uncited.
3. Measure load rate, false triggers and judged scores locally.
4. Gate in CI.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Local, 15 x 2: load rate old description 57%, new 77% (judged run 83%); false triggers 1/18 (wt-006, a silent tabosc4~ patch, which is a fair match for 'no sound'). Judged with the new skill: faithfulness 4.17, relevance 5.00, citation 3.70 (CI baseline 3.38).
Measurement bug found: evaluation/judge.py builds the judge's context as '[heading]\ntext' without the chunk URL, so the judge cannot see that a cited URL came from a retrieved excerpt and marks it fabricated. Verified on dsp-007: every URL the answer cited (manual 2.1.1, 3.3.1, the dac~ page) was among the retrieved chunks, while an earlier judge of the same answer pattern called the dac~ page 'not in provided context'. This affects citation scores in the generation eval, the published baselines and every skill gate, so the fix is a decision for the user, not part of this task.

CI gate 46118109732 with the fixed judge (TASK-5.47): load 80% (fixed), relevance 5.00, faithfulness 3.89, citation 3.78 -> fail. Faithfulness complaints are now about the skill's own claims stated without a retrieved excerpt: [print~]/[bang~] debugging (dsp-001, 007, 012), 'block size sets the FFT channel count' (dsp-009), throw~/catch~ ordering (dsp-001), and the denormal paragraph still read as fact (dsp-013 2/3/2). dsp-005 trial 1 also misstated vd~/delread4~. Next: make each anchor claim conditional on retrieving its excerpt, and put the denormal advice under an explicit 'not in the Pd docs' label.
<!-- SECTION:NOTES:END -->
