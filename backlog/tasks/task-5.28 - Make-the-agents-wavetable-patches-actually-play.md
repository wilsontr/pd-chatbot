---
id: TASK-5.28
title: Make the agent's wavetable patches actually play
status: To Do
assignee:
  - '@trevor'
created_date: '2026-11-27 05:33'
labels:
  - agent
dependencies: []
parent_task_id: TASK-5
priority: medium
type: bug
ordinal: 38000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Asked to "fill a table with one cycle of a sine wave when the patch opens and play it at 220 Hz", the agent produces a patch that runs cleanly but renders silence, and does not diagnose it even though analyze_audio reports "silent" on the same patch.

Recorded 2026-11-26 at the shipped defaults (after TASK-5.27 raised the token budget, so the loop had turns to spare; run .tmp/verify_wavetable.json, 7 turns): timeout then clean then silent, ending on a patch that has no Pd console errors.

The patch it landed on, in outline: loadbang -> msg 0 -> [until] -> [f] -> [+ 1] -> [sel 512] feedback loop -> [expr sin($f1 * 2*pi/512)] -> [tabwrite sine-table], with the counter also driving the tabwrite index; playback is [phasor~ 220] -> [*~ 512] -> [tabread4~ sine-table] -> [*~ 0.2] -> [dac~].

This is a model construction error, not a sandbox or budget defect, and that has been checked rather than assumed:
- Running the agent patch directly in the sandbox: status ok, empty console, peak 0.
- A correct control patch in the same sandbox fills the array with the semicolon form (msg "; sine-table sinesum 512 1", with [table sine-table 512]) and reads it with phasor*512 -> tabread4~: peak 6553.
- The tabosc4~ route also works ([table wt 515] + sinesum + [tabosc4~ wt]): peak 402.
So the sandbox renders table audio; the agent fill loop is wrong. Likely suspects to confirm: the [tabwrite] index/value arrival order in that loop (it writes the previous value at the new index and never writes the last sample), and reading a 512-sample array with [tabread4~] without accounting for the interpolation wrap or for the table size tabosc4~ would require.

It is flaky rather than always broken: docs/PATCH_EVAL.md records p_14 at 0/3 without execution and 2/3 with it, so a fix should raise the rate and pin the failing shape, not just retry until one passes.

Scope: the agent reliably writes a wavetable patch that renders audible 220 Hz audio when the docs support it, with the failure mode measured before and after. A wavetable instructions skill and a prompt rule are both legitimate fixes; choose from evidence. Not in scope: sandbox changes (proven correct above) and anything that changes retrieval ranking.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The failure is reproduced from current code with the command and the exact patch recorded, and the cause is identified as fill-loop, read-side or prompt, with evidence for the cause rather than a guess
- [ ] #2 A control measurement records how often the silent-wavetable output occurs before the change, over at least 3 runs of the same question
- [ ] #3 After the change the same question renders audible 220 Hz audio in at least 2 of 3 runs, measured with the patch spec rather than by eye
- [ ] #4 The patch eval aggregate is recorded against docs/PATCH_EVAL.md (73 percent without execution, 92 percent with) and the 30-entry documentation eval shows no regression
- [ ] #5 Any prompt rule, tool description or skill added is covered by tests or by a skill golden set
<!-- AC:END -->
