---
id: TASK-5.47
title: Show the judge each excerpt's URL so URL citations can be verified
status: Done
assignee:
  - '@claude'
created_date: '2026-11-28 06:41'
updated_date: '2026-11-28 07:23'
labels:
  - eval
  - judge
dependencies: []
parent_task_id: TASK-5
priority: high
ordinal: 58000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
evaluation/judge.py builds the judge's context as '[heading]\ntext' with no URL, while answers cite excerpts by the Source URL printed under each one. The judge therefore cannot tell a real URL citation from an invented one and marks correct ones fabricated: on dsp-007 every URL the answer cited was among its retrieved chunks, yet the judge had called the same dac~ page citation 'not in provided context'. This depresses citation_correctness in the generation eval, the published baseline (docs/EVAL_BASELINE.md, README) and every skill gate. The deterministic claim check (evaluation/claims.py) already compares cited URLs with the excerpts. Found in TASK-5.43.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The judge's context shows each excerpt's Source URL
- [x] #2 The judge controls (evaluation/judge_controls.py) still pass in both directions, and a test pins that the URL reaches the judge
- [x] #3 The 30-entry x 3-trial baseline is re-measured with the fixed judge and docs/EVAL_BASELINE.md, README and the gate calibration notes in evaluation/gates.py agree with it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. judge_context(): each excerpt with its Source URL, used by both judge paths.
2. Unit test plus the live judge controls.
3. Re-measure the 30 x 3 baseline in CI (eval.yml dispatch, trials 3, sample 30), which rewrites docs/EVAL_BASELINE.md.
4. Update README figures and the calibration notes in evaluation/gates.py; check the citation floor still makes sense.
5. Re-gate dsp-debugging (TASK-5.43).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
judge_context() added to evaluation/judge.py and used by score_faithfulness and evaluate_generation. Live judge controls (python -m evaluation.judge_controls): passed in both directions (q_004_correct 5,5,5,5,5; reversed control within bounds). tests/test_judge_rubric.py::TestJudgeSeesUrls.

Baseline re-measured in CI (eval.yml dispatch 46118090172, 30 entries x 3 trials, $2.10), published as docs/EVAL_BASELINE.md (5930d87). Pipeline faithfulness 4.57 ± 0.21, relevance 4.80 ± 0.07, citation 4.70 ± 0.03; agent 4.72 ± 0.09, 4.88 ± 0.02, 4.52 ± 0.07. Citation moved 4.50 -> 4.70 (pipeline) and 4.48 -> 4.52 (agent): the verdict changed from 'matches' to 'trails on citation' (-0.18 ± 0.10). Agent retrieval recall@5 78.9% vs 75.0%. README headline, measured table, verdict paragraph and comparability note updated (tests/test_readme_eval.py 6 pass); gates.py notes updated; floors kept at 4.0/4.5/4.0 (margins wider now) so gate decisions stay comparable.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The judge now sees each excerpt's Source URL, so it can verify URL citations instead of marking correct ones fabricated. Judge controls still pass in both directions. The 30 x 3 baseline was re-measured in CI: pipeline citation rose 4.50 -> 4.70 and agent 4.48 -> 4.52, so the README now says the agent matches the pipeline on faithfulness and relevance and trails it on citation (-0.18 ± 0.10); gate floors unchanged with the reason recorded.
<!-- SECTION:FINAL_SUMMARY:END -->
