---
id: TASK-5.22
title: Correct the faithfulness judge's Pd inlet-order belief and re-measure
status: To Do
assignee: []
created_date: '2026-11-24 17:41'
updated_date: '2026-11-24 17:41'
labels:
  - agent
dependencies: []
references:
  - evaluation/judge.py
  - evaluation/gates.py
  - evaluation/claims.py
  - docs/EVAL.md
parent_task_id: TASK-5
priority: medium
type: bug
ordinal: 31000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The faithfulness judge holds the wrong belief about Pd inlet order, which makes the faithfulness numbers on `[line~]`-style entries partly a measurement of judge error rather than answer quality. It is wrong in **both** directions, so it cannot be fixed by tuning a threshold:

- A self-contradicting answer ("a list such as `1000 0.5` (time, then target)") scores **5/5/5**.
- A correct answer ("`500 1000` would set the target to 500 in the left inlet and the time to 1000 ms in the right. Because the first value goes to the leftmost inlet...") scores **2**.
- The second case was recorded on **both arms**: pipeline `q_004` trial 3 of the 30x3 run ("A list such as `1 500` into the left inlet is distributed left to right, so the `1` goes to the left inlet and starts the ramp") scored 2, and agent A/B run 2 scored 2.

Mechanism: `[line~]`'s excerpt says only "a list of two floats distributes the value over the inlets, as usual in Pd", and the judge appears to resolve "as usual" with the right-to-left rule, which the corpus states for **outlets** only (§2.4.4). So the judge penalises the correct thing and rewards the reversal — the opposite of what the eval needs. TASK-5.20 pinned the model-side failure with deterministic checks, which is why this is now measurement quality rather than a shipping bug: the published comparison (docs/EVAL.md) still holds because both arms share the judge, but the per-entry scores on this rule are unusable and the faithfulness variance is inflated.

Outcome: the rubric states the documented rule (Pd fills an object's inlets left to right from a list arriving at its leftmost inlet; the right-to-left rule is about outlets), the judge is verified in both directions with recorded control answers, and the published 30-entry numbers are re-measured with the corrected rubric — including a stated decision on whether the gate thresholds (`evaluation/gates.py`) still hold.

Risks to handle: teaching the judge the rule must **not** blind it to a reversed example, and a rubric change invalidates the calibration behind `GENERATION_THRESHOLDS`. Both are acceptance criteria below.

Evidence artifacts: the recorded answers are in the untracked `.tmp/eval_521_full.json` from TASK-5.21 (regenerate with `--generation --mode both --sample 30 --trials 3 --output`); `tests/test_claims.py` holds the same phrasings as fixtures and `.tmp/ab_q_004_*.json` holds the A/B runs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The faithfulness rubric states the documented rule (inlets fill left to right from a list at the leftmost inlet; right-to-left applies to outlets) without naming any golden entry answer
- [ ] #2 A control check runs the two recorded q_004 answers (the reversed `1000 0.5` one and the correct `500 1000` one) through the updated judge N>=3 times each and records the scores on the task: the reversed answer must score <= 3 and the correct one >= 4
- [ ] #3 The 30-entry x 3-trial both-arm eval is re-run with the updated judge and the new means/stds are recorded, with the change from the TASK-5.21 baseline (pipeline 4.71 +/- 0.16, agent 4.66 +/- 0.05) stated per arm
- [ ] #4 docs/EVAL.md and the README measured paragraph carry the re-measured numbers, and the task states whether the parity conclusion still holds
- [ ] #5 The gate decision is explicit: either the thresholds still hold unchanged, or new values are measured and recorded with the reason
- [ ] #6 Any judge-prompt or control-check change is covered by a unit test (rubric-contract assertion and/or stored control fixtures)
<!-- AC:END -->
