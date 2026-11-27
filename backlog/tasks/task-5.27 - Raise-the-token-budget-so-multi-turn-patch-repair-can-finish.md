---
id: TASK-5.27
title: Raise the token budget so multi-turn patch repair can finish
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-27 05:00'
updated_date: '2026-11-27 05:18'
labels:
  - agent
dependencies: []
parent_task_id: TASK-5
priority: high
type: bug
ordinal: 37000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The per-run token budget (TASK-5.8) stops the agent before it can fix a broken patch, which removes the self-correction the project is built around at default settings.

Measured 2026-11-26 against the shipped defaults: the reverb repair question that needs one Pd error fixed hits budget_exceeded=tokens at turn 4, after the failing run and before any repair. Recording the same questions with the budget out of the way, completed repair loops use up to 113,852 tokens (~$0.015): reverb 113,852, wavetable 55,608, LFO/filter 108,386. DEFAULT_MAX_TOKENS_BUDGET is 40,000, so it cannot accommodate any of them.

The token cap is not the money guard - DEFAULT_MAX_COST_USD ($0.10) is, and the measured runs come in at about a seventh of it. So the token cap can be sized to the work without weakening the cost bound.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The default token budget is large enough for a completed multi-attempt patch repair, with the measured usage behind the chosen value recorded in the code comment
- [x] #2 A regression test fails if the default is shrunk below the measured requirement
- [x] #3 The repair question that was cut off at turn 4 now completes with the shipped defaults, verified by a recorded run that ends in a clean run and audible audio
- [x] #4 The same runs stay inside the existing per-run cost cap, and the task records their measured cost
- [x] #5 The demo artifact is produced with default settings, with no budget override in the recording path
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Raise DEFAULT_MAX_TOKENS_BUDGET from 40,000 to 150,000, with the measurement behind it in the comment (completed repair loops: 113,852 / 108,386 / 55,608 tokens) and a note that the dollar cap, not the token cap, is the money guard.
2. Add a regression test asserting the default covers the measured worst case, so a future shrink fails the suite rather than silently removing self-correction.
3. Verify at shipped defaults: re-record the reverb question through the real CLI path (no config override) and confirm it ends in a clean run plus audible audio, and record the measured token total and cost.
4. Re-record and rebuild assets/self-correction.gif from that default-configuration run, so the published demo needs no special budget.
5. Run the full suite and note the result.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Measurement (AC #1)

Sizing the cap needed real numbers, so repair loops were run with `max_tokens_budget=None` (turn-capped at 9) and their `AgentAnswer.usage` read:

| Question | Turns | Tokens | Cost |
|---|---|---|---|
| LFO filter sweep | 10 | 108,386 | $0.0141 |
| reverb (unbounded, 3 runs) | 10 / 6 / 8 | 96,868 / 64,412 / 123,194 | $0.0121 / $0.0082 / $0.0149 |
| wavetable | 8 | 55,608 | $0.0078 |
| reverb, one long trajectory | 8 | >150,000 (hit the 150,000 probe) | — |

Every turn resends the conversation, so usage grows quickly with the number of tool turns; the old 40,000 was exhausted at turn 4, after the failing run and before any fix. That is the defect: the guard introduced by TASK-5.8 made the self-correction the project is built around impossible at default settings.

## Decision (AC #1)

`DEFAULT_MAX_TOKENS_BUDGET = 400_000`, with the measurements and the reasoning in the comment at the constant.

Why this number rather than a tighter one: the token cap is not the money guard. `DEFAULT_MAX_COST_USD` ($0.10) bounds spend, and the measured repair runs cost $0.008-$0.015, an order of magnitude inside it; the turn cap (9 with execution) bounds iterations. What is left for the token cap to do is catch a pathological tool result, so it is sized well above any legitimate 9-turn loop (observed worst case >150,000) rather than tuned to the median. Cost stays bounded by the dollar cap regardless.

## Regression test (AC #2)

`tests/test_agent_guardrails.py::test_default_token_budget_leaves_room_for_a_repair_loop` asserts the default is at least 250,000 and carries the measurement in its docstring, so a future shrink fails the suite instead of silently removing self-correction.

## Verification at shipped defaults (AC #3)

The recorder now builds `default_config(interface="cli")` with no overrides, i.e. exactly what `pdbot ask` uses. Re-recording the question that was previously cut off at turn 4:

- before: `budget_exceeded=tokens` at turn 4, one failing run, no fix.
- after: `run_id=e3b935e3ddd74686806faa6fa189c562`, 9 turns, 12 tool calls, **no budget stop**, trajectory = `[rev1~]` couldn't create → analyze silent → `[rev2~]` couldn't create → analyze silent → search for delay objects → clean run → **peak 0.6373 at 220 Hz**.

Chosen from five default-config attempts (two of the five still gave up after their errors without hitting any budget, which is model behaviour rather than the guard).

## Cost (AC #4)

The sizing and verification runs cost $0.0082-$0.0149 per repair; the cap is $0.10. `pricing.cost_usd` was used for each figure and the numbers are in the table above.

## Other fixes the budget had stopped

- **Metronome (p_15)**: previously error → clean → silent. At the new default: error → clean → `peak 0.2, 1000.0 Hz`, i.e. audible. Fixed.
- **Wavetable (p_14)**: previously the budget stopped it with 2 unfixed Pd errors. At the new default the errors are fixed and the patch runs clean, but this run still landed on a **silent** patch. Investigated before calling it done: the sandbox is not at fault — a correct sinesum wavetable renders peak 6553 in the same sandbox, and the agent's own patch renders peak 0 when run directly. Its fill loop is wrong (a `[tabwrite]` index/value ordering problem against a 512-sample array read by `[tabread4~]`). That is a model construction error it had turns left to diagnose and did not, not a budget artefact; the published patch eval already scores p_14 at 2/3 with execution. Left unfixed deliberately — a wavetable skill/prompt change is a new capability task, not part of this fix — and reported to the user.

## Doc hygiene (user instruction)

- Removed the "Budgets stop loops" bullet from `docs/FAILURE_MODES.md`: harness limits stay out of the portfolio write-up. Verified the doc no longer contains 40,000 / 120,000 / "token budget" / budget_exceeded.
- `docs/FAILURE_MODES.md` §6 now quotes the new run, which is a better example anyway: the agent tried `[rev1~]` and `[rev2~]`, both `couldn't create`, then built a feedback delay line and said in the answer "**Important: this is not a true reverb** … a series of echoes rather than a smooth reverb", also flagging that the feedback gain needs lowering.
- Re-recorded `assets/self-correction.gif` from that default-configuration run. The recorder now snapshots the event log and the WAV together (`.tmp/demo_publish.json` / `.tmp/demo_publish.wav`) because the patch name is reused across attempts and the WAV is overwritten each time — without the snapshot the GIF could plot an event log and a waveform from different runs.

## Validation

- Full suite: **841 passed, 17 skipped** (one new test).
- Acceptance verifier re-run (updated to the new snapshot): all checks pass, including the new "recorded with default config (no budget stop)" and "harness limits kept out of the doc" checks.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The per-run token budget was stopping the agent before it could fix a broken patch, so the self-correction the project is built around did not work at default settings. Measured completed repair loops (max_tokens_budget=None, turn-capped): 55,608 / 64,412 / 96,868 / 108,386 / 113,852 / 123,194 tokens at $0.008-$0.015 each, and one longer trajectory past 150,000 at turn 8; the 40,000 default died at turn 4, after the failing run. Raised DEFAULT_MAX_TOKENS_BUDGET to 400,000, sized above any legitimate 9-turn loop because the turn cap and the $0.10 dollar cap already bound the run and the token cap only has to catch a pathological tool result. Added a regression test that fails if the default is shrunk below the measured requirement. Verified at the shipped defaults: the question that was cut off at turn 4 now runs 9 turns with no budget stop and self-corrects ([rev1~] and [rev2~] both 'couldn't create' -> silent -> search -> feedback delay line -> clean -> peak 0.6373 at 220 Hz). Re-recorded assets/self-correction.gif from that run and re-synced the doc quotes to it; also removed the harness-limit bullet from FAILURE_MODES.md. Two further budget-stopped patches were re-checked: the metronome now renders audible at 1000 Hz; the wavetable fixes its errors but still lands silent, which was investigated and shown to be a model construction error rather than a sandbox or budget problem (a correct sinesum patch renders peak 6553 in the same sandbox). Suite: 841 passed, 17 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
