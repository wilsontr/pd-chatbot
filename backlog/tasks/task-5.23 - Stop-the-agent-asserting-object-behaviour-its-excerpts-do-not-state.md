---
id: TASK-5.23
title: Stop the agent asserting object behaviour its excerpts do not state
status: To Do
assignee: []
created_date: '2026-11-24 17:43'
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
- [ ] #1 The current failure is reproduced and recorded on the task: the exact claim the agent makes on q_007, its tool calls, and both the judge score and the deterministic warning that fired
- [ ] #2 The fix is chosen from the evidence and recorded (prompt rule, tool description, or retrieval), with a control A/B of at least 3 runs per variant recording how often the unsupported ordering claim appears
- [ ] #3 The outlet-order-unsupported warning rate on q_007 drops after the change, with numbers before and after recorded
- [ ] #4 The severity decision is explicit: either the warning becomes a gating error with a measured rate low enough not to be flaky, or it stays a warning with the measured rate and the reason recorded
- [ ] #5 The q_006 and q_014 snapshot~ inlet errors are either fixed with the same before/after evidence, or the task records why they are separate and where they are tracked
- [ ] #6 No regression: a re-run of the 30-entry both-arm eval (or a documented sampled re-run) shows 0 claim-check errors, the pipeline gate passing, and the scores recorded against the TASK-5.21 baseline
- [ ] #7 Any prompt rule or prompt-contract assertion added is covered by tests
<!-- AC:END -->
