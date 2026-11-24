---
id: TASK-5.23
title: Stop the agent asserting object behaviour its excerpts do not state
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-24 17:43'
updated_date: '2026-11-24 19:03'
labels:
  - agent
dependencies: []
references:
  - agent/prompt.py
  - agent/tools/docs.py
  - evaluation/claims.py
  - docs/EVAL.md
parent_task_id: TASK-5
priority: medium
type: bug
ordinal: 32000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The agent still asserts documented object behaviour that its retrieved excerpts do not state, and the judge penalises it. The pinned case is `q_007` ("How do [pack] and [unpack] work..."): at 30 entries x 3 trials the agent scored faithfulness **2/4/3 (mean 3.00)** against the pipeline's 4.67 on the same entry, the judge's explanation was "outlet firing order ... beyond documentation", and the deterministic `outlet-order-unsupported` warning fired in **2 of 3 trials**.

The claim itself is true Pd — objects with several outlets send them right to left — but the corpus states it in exactly one place (§2.4.4), and the IEM `[unpack]` page that retrieval returns does not. The agent prompt already carries an attribution rule from TASK-5.1 ("a general chapter that merely mentions an object is not evidence for how that object behaves"), so the rule exists but is not followed reliably: it is abstract, and the model applies the general outlet convention to the specific object anyway. TASK-5.20 pinned this as a *warning* rather than an error because gating on it today would fail the build roughly every other run.

Second, same family, possibly in scope: `q_006` and `q_014` (`[snapshot~]`) scored faithfulness **2** — the answers give the signal/bang inlet arrangement wrongly ("signal into the left inlet", "two inlets"). That is a factual error rather than an unsupported convention, and no deterministic check covers it.

Why it matters: TASK-5.7 will calibrate agent thresholds against the TASK-5.21 baseline, so it needs that baseline to be a prompt-quality floor rather than a known-defect floor. Every unflagged specific also costs citation/faithfulness credit and is the kind of thing a reader notices.

Outcome: a measured prompt or tool-guidance change that makes the attribution rule concrete for object-level conventions, the resulting claim-warning rate recorded, an explicit decision on whether `outlet-order-unsupported` becomes a gating error, and no regression in the 30-entry numbers.

Not in scope: the judge rubric (TASK-5.22) and anything that changes the retrieval stack's ranking.

Evidence artifacts: the recorded answers are in the untracked `.tmp/eval_521_full.json` from TASK-5.21 (regenerate with `--generation --mode both --sample 30 --trials 3 --output`); the warning is `evaluation/claims.py::check_outlet_order_supported`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The current failure is reproduced and recorded on the task: the exact claim the agent makes on q_007, its tool calls, and both the judge score and the deterministic warning that fired
- [x] #2 The fix is chosen from the evidence and recorded (prompt rule, tool description, or retrieval), with a control A/B of at least 3 runs per variant recording how often the unsupported ordering claim appears
- [x] #3 The outlet-order-unsupported warning rate on q_007 drops after the change, with numbers before and after recorded
- [x] #4 The severity decision is explicit: either the warning becomes a gating error with a measured rate low enough not to be flaky, or it stays a warning with the measured rate and the reason recorded
- [x] #5 The q_006 and q_014 snapshot~ inlet errors are either fixed with the same before/after evidence, or the task records why they are separate and where they are tracked
- [x] #6 No regression: a re-run of the 30-entry both-arm eval (or a documented sampled re-run) shows 0 claim-check errors, the pipeline gate passing, and the scores recorded against the TASK-5.21 baseline
- [x] #7 Any prompt rule or prompt-contract assertion added is covered by tests
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce the q_007 failure from the recorded TASK-5.21 artifacts and a fresh probe: exact claim, tool calls, judge score, deterministic warning.
2. Add evaluation/probe.py to run one golden entry through the agent N times and record tool calls, answer, claim checks and judge scores.
3. A/B the shipped prompt against concrete prompt-rule variants (>=3 runs each) and choose the fix from that evidence.
4. Implement the winning prompt/tool change so object-level conventions must be stated by the object's own excerpt.
5. Record before/after warning rates on q_007; decide whether outlet-order-unsupported becomes an error or stays a warning.
6. Measure q_006/q_014 snapshot~ inlet errors under the same change: fix with evidence, or record why they are separate and where they are tracked.
7. Re-run the 30-entry x 3-trial both-arm eval (on top of the TASK-5.22 judge fix) for no regression, 0 claim-check errors and a passing pipeline gate; update docs.
8. Add tests for the prompt rule/contract.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Reproduction (AC #1)

Recorded baseline, TASK-5.21 30x3 (`.tmp/eval_521_full.json`): agent q_007 faithfulness **2/4/3 (mean 3.00)**, `outlet-order-unsupported` warning in **2 of 3** trials. Fresh probe with the shipped prompt (`python -m evaluation.probe q_007 --runs 3`, final rubric): **2/3 warnings**, faithfulness [2, 5, 4], mean 3.67.

The two recorded claims (`.tmp/probe_q007_baseline_finaljudge.json`):
- run 2: "The general Pd rule is that outlets fire right to left, but that isn't in these excerpts, so check it in Pd before relying on it" — flagged as a caveat, still states the convention.
- run 3: "The `[unpack]` outlets send the atoms right to left, which is the outlet ordering the docs describe for objects generally" — attributed to "the docs" generally, while no retrieved excerpt states it.

Tool calls (same in both): one `search_docs` call, `query="pack unpack compound messages lists"`, `object_names=["pack","unpack"]`, `content_type="object_reference"`; returned headings `pack, unpack, pipe, oscparse, list trim` (run 1 added `trigger`). None states outlet order. Judge explanations: "outlet firing order ... beyond documentation" (2), "adds unflagged specifics (outlet firing order...)" (3), "properly flagged as beyond context" (5).

## Fix choice and A/B (AC #2)

`evaluation/probe.py` runs one golden entry N times and records tool calls, answers, deterministic checks and judge scores. Variants were prompt files (`--system-prompt-file`); each tested on q_007 with the shipped prompt as control:

| Variant | Runs | q_007 warning rate | Faithfulness | Notes |
|---|---|---|---|---|
| shipped prompt (control) | 3 | **2/3** | 2, 5, 4 | baseline above |
| A — object-own-excerpt rule + flagging-is-not-enough + count table rows | 5 | **0/5** | 5, 4, 5, 5, 4 | removed the convention claim |
| A2 — A + count rows not message types | 5 | **0/5** | 5, 5, 2, 5, 2 | two 2s were judge errors over-applying the inlet rule to `[unpack]` outlet mapping; fixed by the rubric clarification, not by the prompt |
| A3 — A2 + "one object's documented behaviour is not another's" | 5 | **0/5** | **5, 5, 5, 5, 5** | chosen |

A tool-description variant was prepared but not needed: the prompt rule removed the warning in every run, and the tool description is a less reliable channel. No retrieval change (out of scope).

Implemented in `agent/prompt.py` `_TOOL_RULES` (the pipeline arm's `SYSTEM_PROMPT` comparison stays intact):
- an object's own behaviour must come from its own excerpt; "outlets fire right to left" is a convention stated once for outlets in general and is not evidence about a given object;
- the flagging rule does not make an object-level count/arrangement acceptable — omit it rather than assert it with a caveat, and do not put an unconfirmed count in a pd-patch block;
- a reference inlet table has one row per inlet: count rows, not message types;
- one object's documented behaviour is not another's; do not explain one object's wiring by another object's documented order.

Also fixed the shared `SYSTEM_PROMPT` loophole the re-run exposed: the pipeline answered q_004 with "send the ramp time ... before each target value, for example with a list such as `200 0`". The rule now says never offer a two-element list as a way to send the time before the target, because the list reaches the leftmost inlet first.

## Warning rate before/after (AC #3)

q_007 `outlet-order-unsupported` rate: **2/3** with the shipped prompt (recorded TASK-5.21 and the fresh control), **0/5** under A3, **0/3** with the shipped A3 prompt after implementation, and **0/3** in the final 30-entry x 3-trial run. That is 0/11 after the change.

The judge still scored q_007 3/2/5 (mean 3.33) in the final 30x3 for other claims and judge noise (one 2 was the judge mislabelling correct "leftmost inlet receives last" advice as an inlet-order error); the deterministic warning did not fire. Baseline q_007 was 3.00, so the entry did not regress.

## Severity decision (AC #4)

`outlet-order-unsupported` **stays a warning**. Measured rate after the fix is 0/11 (0/5, 0/3, 0/3), so it is not currently a useful gate. It also does not test falsehood: the claim is true Pd, and the check's support test is retrieval-dependent — a retrieved `[trigger]`/§2.4.4 section satisfies it, so the same answer can be a warning in one run and clean in another. Gating it would gate attribution style, not correctness, and would make the 10-entry CI run flaky. The 0/11 measurement is recorded here for TASK-5.7's agent-threshold calibration.

## q_006 and q_014 (AC #5)

`q_006` (snapshot~): baseline with the final rubric [2, 2, 5]; under A3 [5, 2, 5, 5, 5] — better, not eliminated. Deterministic analysis of the pd-patch JSON: a `[snapshot~]` object with `"inlets": 2` appears in **3/3** baseline answers and **4/5** A3 answers, sometimes while the same answer's prose says the reference lists only one inlet. `q_014`: baseline [5, 5, 5]; under A3 [5, 5, 5, 5, 4], with one answer still saying "two inlets" in prose.

This residual is a diagram/schema problem, not an attribution-prompt problem: more prompt text does not stop the model writing a count it has just disclaimed. It is recorded as separate and tracked in **TASK-5.24** (created by this task) with the measured rates and its own acceptance criteria.

## No regression (AC #6)

Final 30x3 both arms (`.tmp/eval_524_full.json`, command in TASK-5.22):

| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 | p95 | Tools |
|---|---|---|---|---|---|---|---|---|
| pipeline | 4.50 ± 0.13 | 4.81 ± 0.05 | 4.49 ± 0.17 | 30 | $0.0110 | 3.7 s | 5.5 s | 0 |
| agent | 4.66 ± 0.21 | 4.88 ± 0.02 | 4.47 ± 0.07 | 30 | $0.0124 | 5.8 s | 7.8 s | 1.48 |

Against the TASK-5.21 baseline the agent is unchanged on faithfulness (4.66 → 4.66), +0.01 on relevance and −0.03 on citation — all inside judge noise. The recorded run had one claim-check failure, a **false positive**: the agent q_011 answer quoted `[trigger]`'s "output from right to left" sentence while mentioning the hot/cold inlet paradigm, and `check_inlet_order` matched it. Fixed in `evaluation/claims.py` (sentences about output order are the outlet rule), with the recorded sentence as a regression test. Recomputing the answer-only checks on the stored 180 answers gives **0 flagged and no threshold failures** (`.tmp/eval_524_recheck.json`), and the 10-entry CI-shaped run with the final code passed with **0 claim checks** (pipeline 4.20/5.00/4.60, cost $0.1944).

The pipeline gate passes unchanged (faithfulness 4.50 >= 4, relevance 4.81 >= 4.5, citation 4.49 >= 4, scope 100%).

## Tests (AC #7)

- `tests/test_claims.py`: prompt-contract tests for the four agent rules (and that only the agent arm carries them), the shared "never offer a list to send the time first" rule, and regression fixtures for both false positives found during the re-run (verified `0.8 500` list beside time-then-target advice; quoted trigger output sentence).
- `tests/test_probe.py`: aggregation of warning/error counts, per-run warning count, missing scores and unknown entry ids.
- Full suite: **307 passing**.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Stopped the agent asserting object behaviour its excerpts do not state, and measured the fix.

What changed:
- agent/prompt.py (_TOOL_RULES): the attribution rule is now concrete for object-level conventions — an object's own excerpt is the only evidence for its behaviour; a general convention (e.g. 'outlets fire right to left') is not evidence about a specific object; neither flagging nor a caveat makes an unconfirmed inlet/outlet count acceptable (and no unconfirmed count in a pd-patch block); a reference inlet table has one row per inlet; one object's documented behaviour is not another's.
- agent/prompt.py (SYSTEM_PROMPT): closes the loophole the re-run exposed in both arms — never offer a two-element list as a way to send the time before the target.
- evaluation/probe.py: one-entry, N-run probe recording tool calls, answers, deterministic checks and judge scores (used for the A/B).
- evaluation/claims.py: two false positives found during the runs fixed with regression tests (a verified correct list beside time-then-target advice; a quoted '[trigger] ... output from right to left' sentence).
- tests/test_claims.py prompt-contract and false-positive tests; tests/test_probe.py aggregation tests.

Verification:
- Reproduced on q_007: 2/3 runs flagged by outlet-order-unsupported with the shipped prompt (recorded TASK-5.21 and fresh), faithfulness 2, 5, 4; exact claims and the single search_docs call per run recorded in the notes.
- A/B (3-5 runs per variant): shipped 2/3 warnings; variant A 0/5; A2 0/5; final A3 0/5 with faithfulness 5,5,5,5,5. Shipped A3 prompt after implementation: 0/3 warnings, 5,5,5.
- Warning rate before/after: 2/3 -> 0/11 (0/5, 0/3, 0/3; final 30x3 included). Severity decision: keeps warning; reason and measured rate recorded.
- q_006/q_014: improved (q_006 2,2,5 -> 5,2,5,5,5; q_014 5,5,5 -> 5,5,5,5,4) but the '[snapshot~] inlets: 2' pd-patch count still appears in 4/5 q_006 runs, so the residual is recorded and tracked as TASK-5.24.
- No regression: final 30x3 both arms, agent 4.66 +/- 0.21 / 4.88 +/- 0.02 / 4.47 +/- 0.07 vs pipeline 4.50 / 4.81 / 4.49; TASK-5.21 agent baseline was 4.66 / 4.87 / 4.50. Zero claim-check errors after the false-positive fix (recorded answers rechecked); 10-entry CI gate passed with 0 claim checks and the pipeline gate passing.
- Full suite: 307 tests passing.
<!-- SECTION:FINAL_SUMMARY:END -->
