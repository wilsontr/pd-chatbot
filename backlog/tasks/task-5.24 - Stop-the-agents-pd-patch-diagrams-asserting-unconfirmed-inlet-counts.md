---
id: TASK-5.24
title: Stop the agent's pd-patch diagrams asserting unconfirmed inlet counts
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-24 18:31'
updated_date: '2026-11-24 21:52'
labels:
  - agent
dependencies: []
references:
  - agent/patches.py
  - agent/prompt.py
  - evaluation/probe.py
  - evaluation/claims.py
  - evaluation/golden_dataset.json
parent_task_id: TASK-5
priority: medium
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.23 fixed the agent's unsupported outlet-order claim and the `[snapshot~]` prose arrangement, but a residual defect remains: the agent still writes inlet counts into pd-patch blocks that its own excerpts do not state, and sometimes contradicts its own caveat in the same answer.

Measured while implementing TASK-5.23 (5 runs per entry, final prompt, judge `claude-haiku-4-5`): on `q_006`, 4 of 5 answers emitted a `[snapshot~]` object with `"inlets": 2` in the pd-patch JSON, although the IEM reference lists a single inlet row ("1st | bang ... | set <float>") and the prose in the same answer said the diagram's second inlet is not documented. The judge only scored one of those runs 2, so the eval under-reports the error rate. On `q_014`, 1 of 5 answers said "two inlets" in prose.

Why this is separate from TASK-5.23: prompt text measurably stopped the outlet-order claim (2/3 warnings before, 0/5 after) but does not stop the diagram from carrying a count the answer admits it cannot confirm. The durable fix is at the patch boundary — validate or omit counts the excerpts do not support — which needs its own design, measurement and regression run. TASK-5.23 records this residual and tracks it here.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The q_006 `inlets: 2` diagram error is reproduced in N>=3 recorded runs with its rate stated, including the answers whose prose disclaims the count
- [x] #2 A fix is chosen from evidence (pd-patch validation/canonicalisation or a prompt rule) with before/after rates on q_006 and q_014
- [x] #3 After the fix, q_006 faithfulness is >= 4 on N>=3 runs and no `[snapshot~]` pd-patch object carries an unconfirmed inlet count
- [x] #4 The 30-entry both-arm eval still shows 0 claim-check errors and no regression against the TASK-5.23 numbers
- [x] #5 The new rule or validation is covered by tests
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce the q_006 pd-patch count error with a fresh N=3 probe and a deterministic patch-count extractor, recording the rate and the answers whose prose disclaims the count.
2. Add agent/patch_counts.py: parse fixed inlet/outlet counts from an object's retrieved IEM reference and rewrite contradicting pd-patch counts, skipping variable-arity objects (n:/number depends) and any object whose connections would fall out of range. Record a metric per repair.
3. Wire the repair into agent.chat.answer_question (the eval path) and stream_chat (the serving path) so both measure and ship the same canonical answer.
4. Add tests: parser (fixed vs variable counts), repair (wrong/correct/out-of-range/malformed/no excerpt), and both chat paths emitting the repaired count.
5. Re-measure q_006 and q_014 (N>=3): deterministic pd-patch count rate and judge faithfulness before/after.
6. Re-run the 30-entry x 3-trial both-arm eval and compare with the TASK-5.23 numbers (agent 4.66 / 4.88 / 4.47), requiring 0 claim-check errors and a passing pipeline gate.
7. Update the README/EVAL caveat if the residual is closed, and finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Reproduction (AC #1)

Fresh probe with the TASK-5.23 shipping code (`python -m evaluation.probe q_006 --runs 3`, `.tmp/probe_q006_524_before.json`): **3/3** answers emitted a `[snapshot~]` pd-patch object with `"inlets": 2`, while the retrieved IEM reference lists a single inlet row (`1st | bang ... | set <float>`). Both connections went to `dstInlet: 0`, so the second nub was decorative. Judge faithfulness **[2, 5, 5]** — it caught only the first, so the eval under-reports the rate exactly as the task said. The same `inlets: 2` pattern had appeared in 4/5 runs of the TASK-5.23 A3 probe.

`q_014` before (TASK-5.23 A3 probe, 5 runs): 1/5 answers carried `"inlets": 2` in a pd-patch and 1/5 said "two inlets" in prose; faithfulness [5, 5, 5, 5, 4].

## Fix (AC #2)

Chosen from evidence: prompt text alone did not stop the JSON count, so the durable fix is a **patch-boundary canonicalisation**, plus a small prompt rule for the leftover prose.

- **`agent/patch_counts.py`** parses fixed inlet/outlet counts from the *retrieved* object-reference excerpts (`Inlets:`/`Outlets:` rows), indexes them by `object_name`, and rewrites a contradicting pd-patch count before the answer is judged or served. Safety rules (all tested): variable-arity objects (`n:`/"number depends", e.g. `pack`, `triggers`'s outlets, `dac~`) are untouched; objects the excerpts do not cover are untouched; conflicting sources are skipped; malformed JSON is left to the validator; non-obj boxes are ignored; and an object whose connection would fall outside the repaired count is not shrunk (so a nub the model wired cannot silently disappear). Metric: `Custom/PdPatch/CountRepaired`.
- Wired into `agent.chat.answer_question` (the eval path) and `stream_chat` (the serving path); the generation eval applies the same repair to **both** arms before judging, so the pipeline baseline is not disadvantaged. `evaluation/probe.py` mirrors the shipped path.
- Prompt (agent only): "Never refer to a 'right inlet' or 'second inlet' for an object whose reference table lists only one inlet row." Repair alone left q_006 at [5, 5, 2, 5, 5] because the prose still mentioned a right inlet; with the rule the shipped prompt measured [5, 5, 5, 5, 5].

## Before/after (AC #2, #3)

| Probe | Before (`inlets: 2` / wrong count) | After (shipped) |
|---|---|---|
| q_006, 5 runs | 4/5 (and 3/3 in the fresh before probe) | **0/5**, all `inlets: 1` |
| q_014, 3-5 runs | 1/5 | **0/3** |

- q_006 faithfulness: [5, 2, 5, 5, 5] before → **[5, 5, 5, 5, 5]** after; the fresh before run was [2, 5, 5].
- q_014 faithfulness: [5, 5, 5, 5, 4] before → [4, 5, 5] after (all >= 4; the earlier 2s were unrelated beyond-context claims about line~/env~, now largely gone).
- Full 30-entry x 3-trial run: **0 of 180** pd-patch objects carry a `[snapshot~]` count other than 1.

## No regression (AC #4)

Full re-run (`.tmp/eval_524b_full.json`, cost **$2.1024**, 30 entries x 3 trials both arms):

| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 | p95 | Tools |
|---|---|---|---|---|---|---|---|---|
| pipeline | 4.63 ± 0.14 | 4.81 ± 0.02 | 4.50 ± 0.09 | 30 | $0.0110 | 3.7 s | 5.4 s | 0 |
| agent | 4.61 ± 0.11 | 4.86 ± 0.05 | 4.48 ± 0.10 | 30 | $0.0123 | 5.5 s | 7.6 s | 1.50 |

Against TASK-5.23 (agent 4.66 / 4.88 / 4.47): faith −0.05, relevance −0.02, citation +0.01 — all within judge noise. Against TASK-5.21: pipeline 4.71 → 4.63, agent 4.66 → 4.61. Paired per-trial deltas: faith −0.02 ± 0.12, relevance +0.05 ± 0.05, citation −0.02 ± 0.02 → parity still holds.

Claim checks: the recorded run had **one** violation, a false positive on the pipeline q_004 answer ("the excerpt ... does not describe a list as a way to set the time before the target, so I would not rely on a list"), and the CI-shaped run found a second false positive on the agent q_004 answer (a parenthetical "(the time before the target)" describing two separate message boxes, not a list). Both are prose-only heuristic misses, fixed in `evaluation/claims.py` with regression fixtures (a list-rejection guard and a "then"-plus-number-pair requirement for the parenthetical). Recomputing the answer-only checks on the recorded 180 answers gives **0 errors**; the only remaining flags are **2 warnings** (pipeline q_007 outlet-order, published only, not gated). The **10-entry CI gate passes with 0 claim checks** (pipeline 4.40 / 5.00 / 4.70, cost $0.2019).

## Docs

- `docs/EVAL.md` regenerated from the rechecked final results; `README.md` measured table/paragraph, retrieval numbers (single-run 76.1% recall@5, with a noise caveat), the caveat that closes the snapshot~ residual, and a new pd-patch canonicalisation paragraph. `evaluation/gates.py` baseline docstring updated to 4.63 / 4.81 / 4.50.

## Tests (AC #5)

- `tests/test_patch_counts.py` (19 tests): parser for fixed vs variable-arity vs missing sections, conflicting sources, manual chunks, wrong/correct counts, both fields, out-of-range connection skip, in-range repair, malformed JSON, non-obj boxes, multiple blocks, no excerpts, and repaired blocks still pass `PdSchema`.
- `tests/test_agent_chat.py`: both `stream_chat` (artifact + Done answer) and `answer_question` emit the repaired count.
- `tests/test_agent_eval.py`: both arms are canonicalised before judging (no agent-only advantage).
- `tests/test_claims.py`: prompt-contract test for the single-inlet prose rule; regression tests for the two new false positives.
- Full suite: **332 passing**.

## AC #1: the disclaiming answer

The fresh before probe (`.tmp/probe_q006_524_before.json`, shipped TASK-5.23 prompt, run 1) emits `"inlets": 2` for `[snapshot~]` while its own prose says: "Note that the `[snapshot~]` inlet and outlet counts in the patch are taken from the reference, which lists one inlet row and one outlet row." The reference indeed lists one row for each; the diagram contradicts the sentence in the same answer. A recorded A4-variant run states it even more plainly: "The inlet count of `[snapshot~]` is shown here as 2 ... but the reference lists only one inlet row ..., so the second inlet in the diagram is not something the docs describe." Both are in the untracked `.tmp` probes; the numeric rate is 3/3 in the fresh probe and 4/5 in the TASK-5.23 A3 probe.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Canonicalised pd-patch inlet/outlet counts against the retrieved object reference, so the agent stops writing counts its excerpts contradict.

What changed:
- agent/patch_counts.py: parses fixed counts from the retrieved IEM reference and rewrites contradicting pd-patch counts before judging/serving; variable-arity objects, uncovered objects, conflicting sources, malformed JSON and out-of-range connections are left alone; metric Custom/PdPatch/CountRepaired.
- Wired into agent.chat.answer_question (eval) and stream_chat (serving); the generation eval applies the same repair to both arms so the pipeline baseline is not disadvantaged; the probe mirrors it.
- agent/prompt.py: never refer to a 'right inlet'/'second inlet' for an object whose reference lists one inlet row (repair alone left the prose error).
- evaluation/claims.py: two prose-only false positives found by the re-runs (a sentence rejecting the list approach; a '(time before target)' parenthetical about separate message boxes) fixed with regression fixtures.
- README/EVAL/gates docs updated; evaluation/gates.py baseline is now 4.63 / 4.81 / 4.50.
- tests/test_patch_counts.py added; chat, eval and prompt-contract tests extended.

Verification:
- Before: q_006 [snapshot~] inlets: 2 in 3/3 fresh runs (and 4/5 in the TASK-5.23 A3 probe), judged 2/5/5; q_014 1/5 wrong. After: 0/5 and 0/3 wrong, q_006 5/5/5/5/5, q_014 4/5/5; 0/180 pd-patch objects wrong in the final 30x3.
- Full 30x3 both arms: pipeline 4.63 +/- 0.14 / 4.81 +/- 0.02 / 4.50 +/- 0.09; agent 4.61 +/- 0.11 / 4.86 +/- 0.05 / 4.48 +/- 0.10 (TASK-5.23: 4.66 / 4.88 / 4.47, all within noise); parity holds; cost $2.1024.
- 0 claim-check errors after the false-positive fixes (recorded answers rechecked), 2 warnings (pipeline q_007, published only); 10-entry CI gate passes with 0 claim checks.
- 332 tests passing.
<!-- SECTION:FINAL_SUMMARY:END -->
