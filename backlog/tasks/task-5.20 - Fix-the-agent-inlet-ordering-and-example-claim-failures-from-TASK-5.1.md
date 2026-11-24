---
id: TASK-5.20
title: Fix the agent inlet-ordering and example-claim failures from TASK-5.1
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-24 09:09'
updated_date: '2026-11-24 10:20'
labels:
  - agent
dependencies: []
references:
  - agent/prompt.py
  - agent/tools/docs.py
  - evaluation/golden_dataset.json
  - docs/EVAL.md
parent_task_id: TASK-5
priority: medium
type: bug
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.1's pipeline-vs-agent generation eval found two reproducible answer-quality failures that the pipeline arm did not have on the same entries. They are the kind of error the agent's freedom introduces, so they need to be fixed and pinned with regression coverage.

**1. `[line~]` example (golden entry `q_004`)** — "How does [line~] work and what's the gotcha with its right inlet?" The agent's answer included "send the time and the target together, for example a list `100 500`", which implies right-to-left distribution across the inlets. Pd fills list inlets left to right, so that example is wrong. The judge scored faithfulness 2 ("Critical error: list order example contradicts documented inlet distribution"). The rest of the answer (the right-inlet-does-not-remember gotcha) was correct.

**2. `[unpack]` misattribution (golden entry `q_007`)** — "How do [pack] and [unpack] work for assembling compound messages?" The agent retrieved "Chapter 2 > 2.8 Subpatches" and stretched it to support a claim that `[unpack]`'s outlets appear right to left, then cited it. Judge: faithfulness 2, citation 2. The tool-guidance and attribution rules added during TASK-5.1 fixed this case in the re-run, but nothing pins it against recurrence.

Context:
- The agent prompt is `agent/prompt.py` (`AGENT_SYSTEM_PROMPT` = shipped prompt + `_TOOL_RULES`); the loop and tool are `agent/loop.py` and `agent/tools/docs.py`.
- Reproduce an entry with `python -m evaluation.evaluate --generation --mode agent --sample 10`, or by draining `agent.chat.answer_question` for the one question and reading `loop.state.answer` plus the tool calls.
- Golden entries carry `reference_answer` and `relevant_chunk_ids`; `q_004`'s reference answer states the correct list behaviour, which is what the answer should be checked against.

Outcome: the reproducible failures are fixed (prompt, tool description or a deterministic check — decide from the evidence), both cases are covered by the eval so a recurrence shows up in CI, and the report states the re-measured scores. If a failure turns out to be model behaviour that cannot be fixed cheaply, record that finding on the task and pin the case as a known limitation with an explicit threshold decision.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `q_004` is reproduced from current code with the command and answer recorded on the task, and the cause (prompt, tool schema or model behaviour) is identified
- [x] #2 The `[line~]` answer no longer makes the right-to-left list claim, and that entry scores faithfulness >= 4 in the agent eval
- [x] #3 Regression coverage exists for both `q_004` and `q_007`, so a recurrence fails or is flagged by the eval rather than being noticed by hand
- [x] #4 The 10-entry both-arm eval is re-run, the agent numbers are recorded on the task, and the pipeline gate still passes
- [x] #5 Automated tests cover any new deterministic check or prompt-contract assertion
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce q_004 through agent.chat.answer_question with the shipped config; record the answer, the tool calls and the judge scores on the task.
2. Identify the cause from the evidence (prompt gap vs tool schema vs model behaviour) and fix it in the prompt/tool guidance.
3. Add a deterministic claim check for the two known failure modes, wired into the generation eval so a recurrence is flagged/fails rather than caught by hand.
4. Re-run the eval on both arms and record the re-measured numbers on the task.
5. Add unit tests pinning the prompt contract and the claim check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Reproduction (AC #1)

Reproduced from current code before the fix. The output JSON carries the answers (this task added that) so an entry can be read after the fact:

```
PYTHONPATH=. python -m evaluation.evaluate --generation --mode both --sample 10 --output /tmp/eval.json
python -c "import json;d=json.load(open('/tmp/eval.json'));print([e['answer'] for e in d['generation']['entries'] if e['id']=='q_004' and e['arm']=='agent'][0])"
```

`q_004`, agent arm, shipped prompt at the start of this task:

> ... The fix is to always send the time first, or to send a list such as `1000 0.5` (time, then target, with the list distributed across the inlets as described above). Example of a correct ramp to 0.5 over 1000 ms, using a message box `1000 0.5` (a list, so the right inlet gets the time first).

That is the TASK-5.1 failure unchanged: Pd fills inlets left to right, so `1000 0.5` is target 1000 over 0.5 ms. **The judge scored that answer 5/5/5** — it did not notice.

A/B on the same entry, same judge (`claude-haiku-4-5`), 5 runs per prompt:

| Prompt | makes the reversed-list claim | judge faithfulness <= 2 |
|---|---|---|
| shipped before this task | **2/5** runs | 0/5 — the judge missed both |
| with the inlet/outlet rule | **0/5** runs | 1/5 — a false positive on a verified-correct `500 1000` example |

## Cause

Prompt gap plus model behaviour; the tool schema is not implicated (`search_docs` returned the `[line~]` reference page on every run):
- The `[line~]` text says only "a list of two floats distributes the value over the inlets, as usual in Pd" — it never states the direction, and the only ordering rule in the corpus ("all objects ... send messages out more than one outlet ... right to left", §2.4.4) is about **outlets**. The model applies the outlet rule to inlets, and its example then contradicts its own prose.
- The judge is not a usable pin for this claim: it scored the self-contradicting answer 5/5/5 and scored a verified-correct answer 2. It is wrong in both directions.

## Fix

- `agent/prompt.py`: new "Inlet and outlet order (these are different rules — never mix them up)" block in `SYSTEM_PROMPT` — shared by both arms, so the comparison stays honest: inlets fill left to right from a list; right-to-left is about outlets; a list example must obey the distribution; advise one inlet before another as separate messages.
- `evaluation/claims.py`: deterministic offline checks, no judge involved —
  - corpus-wide: a list/inlet filling right to left (skipping the correct outlet rule, section titles that cite it, and correct "send them separately" advice), and a URL no retrieved excerpt contains;
  - pinned entries: `q_004`'s reversed list example (recorded prose, the `(time, then target)` parenthetical, and a self-contradicting "ramp to X over Y ms" + `Y X` literal); `q_007`'s claim about an object the excerpts do not cover (its recorded failure stretched "2.8 Subpatches", which never mentions `[unpack]`), plus a warning for an outlet-ordering claim no excerpt states.
- Wired into `run_generation_eval` (per entry, per trial), `evaluation/gates.py`, the report ("Deterministic claim checks") and the job summary. Errors fail the gate for **every** arm (deterministic, no calibration needed); warnings are published only, because the model makes the unsupported-ordering claim often enough that gating it would be flaky.

Calibration of the checks against real answers: on 360 answers (two 30-entry x 2-arm x 3-trial runs) the answer-only checks flag **0** answers; every flag from the first seeded runs was a false positive, and each drove a refinement: "the time comes from the right inlet", a cited section title ("2.4.4 Hot and cold inlets and right to left outlet order") and a sentence about MAX's ordering are not inlet-ordering claims, "send the time and then the target as two separate messages, not as a single list" is the correct advice, and `https` vs `http` on the same page is not an invented URL.

## Re-run (AC #2, #4)

10-entry CI prefix, both arms, 1 trial, gate on: **passed** (claim violations: 0).

| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 | p95 |
|---|---|---|---|---|---|---|---|
| pipeline | 4.40 | 5.00 | 4.90 | 10 | $0.0099 | 3.1 s | 4.5 s |
| agent | 3.70 | 5.00 | 4.60 | 10 | $0.0087 | 4.5 s | 7.8 s |

In that single trial the agent's `q_004` was scored 2 by the judge on an answer re-checked against the reference and found correct (the A/B false positive above), so a one-trial per-entry score for this entry is not a usable signal. The 30-entry x 3-trial run (TASK-5.21) gives the per-entry mean that is quotable; see the 5.21 notes for the table. The deterministic checks — which do not depend on the judge — flagged nothing on `q_004` in any trial.

## Tests (AC #5)

`tests/test_claims.py` (33 tests): true positives for each check, every recorded false positive as a regression case, the prompt contract (the rule is in both `SYSTEM_PROMPT` and `AGENT_SYSTEM_PROMPT`), error-vs-warning severity, the gate failing on an error in any arm, and trial-duplicate dedupe. Full suite: 272 passing.

**AC #2 evidence at 30 entries x 3 trials** (the TASK-5.21 run): `q_004` agent faithfulness **5, 5, 5 (mean 5.00)**, citation 5/4/5, and zero deterministic claim violations on any of the three answers. The pipeline arm scored 5/5/2 on the same entry — the single 2 is the judge false positive on a correct left-to-right answer shown above.

Correction to the counts above: after the last two false-positive fixes, `tests/test_claims.py` has 35 tests and the full suite 276 passing.

**Reproduction route, precisely:** the pre-fix answer quoted above was produced by a throwaway driver over `agent.loop.AgentLoop` + `agent.chat.default_registry` — the same loop, prompt and tool registry that `evaluation.generation._agent_answer` drives — and the same failure was then re-confirmed by the A/B control runs (shipped prompt: 5 runs, 2 flagged). The `python -m evaluation.evaluate ... --output` command above is the harness route for reading an entry's answer (the output JSON now stores answers); both paths run identical code, so either reproduces the entry.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed both TASK-5.1 agent answer-quality failures and pinned them.

**q_004 ([line~] list order).** Reproduced from the shipped prompt (agent loop): the answer gave the list `1000 0.5` as "time, then target", contradicting Pd's left-to-right inlet distribution — and the judge scored it 5/5/5, so the judge cannot be the pin. Cause: a prompt gap (the only ordering rule in the corpus is about *outlets*) plus model behaviour; the tool was returning the right page. Fix: a shared "inlet and outlet order" rule in `agent/prompt.py` (both arms). A/B, 5 runs each: 2/5 reversed claims before, 0/5 after; q_004 agent faithfulness 5/5/5 at 30 entries x 3 trials.

**q_007 ([unpack] attribution).** The recorded failure (stretching a "2.8 Subpatches" section that never mentions unpack) did not recur and is now pinned.

**Pinning.** New `evaluation/claims.py`: offline deterministic checks (list fills inlets right to left, an object discussed with no excerpt covering it, an outlet ordering no excerpt states, a URL no excerpt contains, plus the pinned q_004/q_007 rules), wired into `run_generation_eval`, the gate (errors fail for every arm; warnings publish only), the report and the job summary. Calibrated against 360 recorded answers: every false positive from the seeded runs became a regression test (section-title citations, MAX comparisons, correct "send them separately" advice, http/https on the same page).

**Verification.** 276 tests passing (`tests/test_claims.py`, 35); 10-entry both-arm gate passes with 0 claim violations; 30-entry x 3-trial run recorded on the task. Known limitation recorded: the judge is wrong about this rule in both directions, and the agent still asserts some unsupported specifics (q_006/q_014 snapshot~, q_007 outlet order) — carried to TASK-5.7.
<!-- SECTION:FINAL_SUMMARY:END -->
