---
id: TASK-5.22
title: Correct the faithfulness judge's Pd inlet-order belief and re-measure
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-24 17:41'
updated_date: '2026-11-24 19:03'
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
- [x] #1 The faithfulness rubric states the documented rule (inlets fill left to right from a list at the leftmost inlet; right-to-left applies to outlets) without naming any golden entry answer
- [x] #2 A control check runs the two recorded q_004 answers (the reversed `1000 0.5` one and the correct `500 1000` one) through the updated judge N>=3 times each and records the scores on the task: the reversed answer must score <= 3 and the correct one >= 4
- [x] #3 The 30-entry x 3-trial both-arm eval is re-run with the updated judge and the new means/stds are recorded, with the change from the TASK-5.21 baseline (pipeline 4.71 +/- 0.16, agent 4.66 +/- 0.05) stated per arm
- [x] #4 docs/EVAL.md and the README measured paragraph carry the re-measured numbers, and the task states whether the parity conclusion still holds
- [x] #5 The gate decision is explicit: either the thresholds still hold unchanged, or new values are measured and recorded with the reason
- [x] #6 Any judge-prompt or control-check change is covered by a unit test (rubric-contract assertion and/or stored control fixtures)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add the documented inlet/outlet ordering rule to the faithfulness rubric in evaluation/judge.py, generic and free of any golden-entry answer.
2. Add evaluation/judge_controls.py with the two recorded q_004 control answers (reversed and correct) plus their context, score each N>=3 times, and enforce the bounds (reversed <= 3, correct >= 4); CLI records JSON.
3. Add tests/test_judge_rubric.py: rubric-contract assertion, no golden-answer literals, control-fixture direction checks, and summary/bound semantics.
4. Run the controls (5 runs each) and record every score on the task.
5. Re-run the 30-entry x 3-trial both-arm eval with the corrected judge and record new means/stds against the TASK-5.21 baseline (pipeline 4.71 +/- 0.16, agent 4.66 +/- 0.05).
6. Decide the gate thresholds explicitly (unchanged or re-measured).
7. Update docs/EVAL.md and the README measured paragraph, state whether parity holds, and finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Rubric correction (AC #1)

`evaluation/judge.py` `FAITHFULNESS_PROMPT` now has a "Pd ordering facts" block:
- a list arriving at the leftmost inlet fills the inlets left to right;
- the right-to-left rule is about outlets and says nothing about inlet order;
- a reversed list example ("first element reaches a right-hand inlet", or a stated result that puts a later element in the leftmost inlet) must be penalised even when the answer cites the right-to-left convention;
- a correct left-to-right description must not be penalised just because the excerpt says "as usual in Pd";
- the rule is about one list at the leftmost inlet: separate messages wired to separate inlets are not a list example and patch connections must not be read as a reversed list; outlet firing order and left-to-right element→outlet mapping are not inlet-order errors;
- the paragraph explicitly does not relax the rest of the rubric.

No golden-entry answer or number appears in the rubric; `tests/test_judge_rubric.py` enforces that ("`1000 0.5`", "`500 1000`", "time, then target" are asserted absent).

## Controls (AC #2)

`evaluation/judge_controls.py` + `evaluation/judge_controls.json` hold the two recorded q_004 answers and the agent retrieval context they cite (`[line~]`, `[line]`, `[outlet~]`, `[inlet~]`, `[vline~]`), with per-run bounds (reversed never > 3, correct never < 4). CLI: `python -m evaluation.judge_controls --runs 5 --output ...`.

5 runs each, judge `claude-haiku-4-5`, final rubric:

| Control | Scores | Mean | Bound |
|---|---|---|---|
| reversed (`1000 0.5`, "time, then target" — recorded in `.tmp/notes_520.md`, scored 5/5/5 before) | 1, 1, 2, 1, 1 | 1.2 | every run <= 3 |
| correct (`500 1000` ... "the first value goes to the leftmost inlet" — `.tmp/ab_q_004_new.json` run 2, scored 2 before) | 5, 5, 5, 5, 5 | 5.0 | every run >= 4 |

Design note: the first control run used the *pipeline* q_004 context, which lacks the `[vline~]`/`[line]` pages the correct answer cites, and the judge scored it 2 for those unrelated beyond-context claims — not for inlet order. With the context the answer was actually judged against (the agent's own retrieval), it scores 5. The fixture stores that context and says so.

## Re-measurement (AC #3)

Command (full re-run, final code):

```
PYTHONPATH=. python -m evaluation.evaluate --all --mode both --agent-retrieval \
  --sample 30 --trials 3 --gate --output .tmp/eval_524_full.json \
  --summary .tmp/eval_524_summary.md --report .tmp/eval_524_report.md
```

30 entries, both arms, 3 trials, judge `claude-haiku-4-5`, measured cost **$2.1021**:

| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 | p95 |
|---|---|---|---|---|---|---|---|
| pipeline | 4.50 ± 0.13 | 4.81 ± 0.05 | 4.49 ± 0.17 | 30 | $0.0110 | 3.7 s | 5.5 s |
| agent | 4.66 ± 0.21 | 4.88 ± 0.02 | 4.47 ± 0.07 | 30 | $0.0124 | 5.8 s | 7.8 s |

Change from the TASK-5.21 baseline (pipeline 4.71 ± 0.16, agent 4.66 ± 0.05):
- pipeline: faithfulness **−0.21**, relevance −0.01, citation −0.11
- agent: faithfulness **0.00**, relevance +0.01, citation −0.03

The pipeline faithfulness move is not a rubric effect: the pipeline prompt changed only by the TASK-5.23 line-list loophole sentence (which does not touch q_007), and unchanged-prompt entries moved by up to 2.33 points between runs (pipeline q_007 5.00 → 2.33; per-entry delta sd 0.65). An intermediate judge-only run (`.tmp/eval_523_full.json`, agent prompt already fixed, pipeline prompt unchanged) measured pipeline 4.59 ± 0.16 and agent 4.64 ± 0.18, with citation 4.56/4.36 — the same order of judge noise. The per-trial paired deltas in the final run are faith +0.16 ± 0.27, relevance +0.07 ± 0.04, citation −0.02 ± 0.13.

## Docs and parity (AC #4)

- `docs/EVAL.md` regenerated from `.tmp/eval_524_rechecked.json` with the final renderer; the reported run's only claim error was a false positive (agent q_011 quoted `[trigger]`'s "output from right to left" while mentioning the hot/cold inlet paradigm), fixed in `evaluation/claims.py`, and the answer-only checks were recomputed from the stored answers (0 flagged, gate passes).
- `README.md` measured paragraph carries the new table, deltas, retrieval MRR (0.749 vs 0.696), cost/latency and the caveats.
- **Parity conclusion still holds**: the agent matches the pipeline on all three dimensions; faithfulness is nominally +0.16 but its spread is ±0.27, relevance +0.07 is below the 0.1 threshold, citation −0.02 is level.

## Gate decision (AC #5)

`GENERATION_THRESHOLDS` stay unchanged: faithfulness >= 4.0, relevance >= 4.5, citation_correctness >= 4.0, scope >= 0.9. The corrected-rubric pipeline means keep margins of 0.50 / 0.31 / 0.49 and scope is 100%, so the calibration still holds; citation remains a collapse floor by design. `evaluation/gates.py`'s docstring now records the re-measured baseline instead of the pre-correction figures.

Recorded evidence that the gate passes with the final code: the 10-entry CI-shaped run (`--generation --mode both --sample 10 --trials 1 --gate`) passed with 0 claim checks, pipeline 4.20 / 5.00 / 4.60, cost $0.1944. The full run's only failure was the false positive above; rechecking its stored answers gives 0 claim errors and no threshold failures (`.tmp/eval_524_recheck.json`).

## Tests (AC #6)

`tests/test_judge_rubric.py` (20 tests): rubric-contract assertions for both directions and the list/outlet clarifications; no golden-answer literal in the rubric; fixture provenance and direction; per-run bounds (including "no scores fails"); summary records every score; `run_controls` calls the judge N times per control (monkeypatched) and reports a wrong-direction judge as failing. Full suite: **307 passing**.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Corrected the faithfulness judge's Pd inlet-order belief and re-measured the published comparison.

What changed:
- evaluation/judge.py: the faithfulness rubric now states Pd's documented ordering rule (a list at the leftmost inlet fills inlets left to right; right-to-left is about outlets), says a reversed list must be penalised and a correct left-to-right description must not, and clarifies that separate messages/patch wiring and element→outlet mapping are not list examples. No golden-entry answer or number appears in it.
- evaluation/judge_controls.{py,json}: the two recorded q_004 answers are stored as controls with the context they were judged against; each is scored N times and must stay within per-run bounds. The CLI records every score.
- evaluation/gates.py: docstring now carries the re-measured baseline; thresholds unchanged.
- docs/EVAL.md regenerated and the README measured paragraph updated; evaluation/evaluate.py's report text points at the controls.
- tests/test_judge_rubric.py added (20 tests).

Verification:
- Controls, 5 runs each: reversed 1,1,2,1,1 (all <= 3); correct 5,5,5,5,5 (all >= 4). Recorded in .tmp/judge_controls_522.json; re-run at 3 each also passed.
- 30 entries x 3 trials both arms, cost $2.1021: pipeline faithfulness 4.50 +/- 0.13, relevance 4.81 +/- 0.05, citation 4.49 +/- 0.17; agent 4.66 +/- 0.21, 4.88 +/- 0.02, 4.47 +/- 0.07. Versus the TASK-5.21 baseline: pipeline -0.21 / -0.01 / -0.11, agent 0.00 / +0.01 / -0.03. Parity still holds (faith +0.16 +/- 0.27, rel +0.07 +/- 0.04, citation -0.02 +/- 0.13).
- Gate decision: thresholds unchanged and passing (margins 0.50 / 0.31 / 0.49; scope 100%). The full run's only claim error was a check false positive later fixed; recomputing the stored answers gives 0 errors, and a 10-entry CI-shaped run passed with 0 claim checks (cost $0.1944).
- Full suite: 307 tests passing.
<!-- SECTION:FINAL_SUMMARY:END -->
