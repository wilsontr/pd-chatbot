---
id: TASK-5.28
title: Make the agent's wavetable patches actually play
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 05:33'
updated_date: '2026-11-27 17:39'
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
- [x] #1 The failure is reproduced from current code with the command and the exact patch recorded, and the cause is identified as fill-loop, read-side or prompt, with evidence for the cause rather than a guess
- [x] #2 A control measurement records how often the silent-wavetable output occurs before the change, over at least 3 runs of the same question
- [x] #3 After the change the same question renders audible 220 Hz audio in at least 2 of 3 runs, measured with the patch spec rather than by eye
- [x] #4 The patch eval aggregate is recorded against docs/PATCH_EVAL.md (73 percent without execution, 92 percent with) and the 30-entry documentation eval shows no regression
- [x] #5 Any prompt rule, tool description or skill added is covered by tests or by a skill golden set
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce: score the recorded final patch (.tmp/verify_wavetable.json) in the sandbox, then score single-change variants and read the table back with [tabread]->[print] to separate fill-loop from read-side causes (.tmp/t528/diagnose.py).
2. Control: python -m evaluation.patch_eval --task p_14 --arm with --trials 6 at HEAD b9d005f, classify each failure (silent / wrong pitch / error).
3. Fix from the evidence: a wavetables skill (documented sinesum + tabosc4~ route first, a correct [until] fill loop only when a computed shape is needed, read-back check) and a general execution-prompt rule: when a run is silent, print the suspect values instead of guessing.
4. Pin the failing shape: the recorded patch and the fixed loop as sandbox regression tests; prompt-rule test; skill golden set; bundled example checked against the p_14 spec.
5. Gate and activate the skill with evaluation.skills, then re-measure p_14 x6, the full patch eval x3 and the 30-entry doc eval; record against docs/PATCH_EVAL.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Reproduction and cause (AC #1)

Command: `venv/bin/python .tmp/t528/diagnose.py` scores the final patch from .tmp/verify_wavetable.json (revision 3) against the p_14 spec in the sandbox, then single-change variants, and reads the table back with [loadbang]->[delay 10]->[idx(->[tabread]->[print] (box limit forces dropping the playback half for the read-back).

Recorded patch, exactly: loadbang->[0(; [0(->[f] right inlet AND [0(->[until] left; [f]->[+ 1]->[f] right; [f]->[expr sin($f1 * 6.2831853 / 512)]->[tabwrite sine-table] left; [f]->[tabwrite] right; [f]->[sel 512]->[until] right; [table sine-table 512]; [phasor~ 220]->[*~ 512]->[tabread4~ sine-table]->[*~ 0.2]->[dac~] L+R.

Results: recorded = clean, silent; table[128] reads 0. Changing [0( to 512 alone: still 0. Adding the [until]->[f] wire alone: still 0. Both together (count 512 into [until], [until]->[f]): table[128]=0.999925 and the patch PASSES the 220 Hz spec with the unchanged read chain.

Cause: fill loop, not read side and not the sandbox. Two defects: (1) revision 3 dropped the [until]->[f] wire that revision 1 had, so the counter never steps; (2) the same [0( that resets [f] is sent to [until], which means zero iterations. Minor third defect: fanning [f] to both [tabwrite] inlets writes each value one index early (table[0]=sin(2pi/512)); audible either way, fixed with [t f f]. The [phasor~]*512->[tabread4~] read side was never at fault.

## Control (AC #2)

`python -m evaluation.patch_eval --task p_14 --arm with --trials 6` at HEAD b9d005f (before any change): **1/6 pass**, all 5 failures **silent**, 50% clean (.tmp/t528/before_p14.json). All 5 failures are fill-side: t1 count wired to [until]'s RIGHT (stop) inlet; t2/t5/t6 a [sinesum ...( message box without the leading ';' and wired to nothing (one also sized sinesum 515 for an array define 515); t4 no table and no fill at all. The single pass used '; sinetab sinesum 512 1' + [tabosc4~].

## Fix (chosen from that evidence)

- New skill skills/wavetables: documented sinesum + [tabosc4~] route first (tabosc4~ page: guard points, sinesum), phasor~->*~N->[+~ 1]->tabread4~ for a sinesum table, and the counter loop's four rules (count into [until] left, [until]->[f], separate reset, [t f f]) only for shapes sinesum cannot make; read-back check before rewiring.
- General execution-prompt rule: when a run is clean but silent or wrong, print the suspect value (for a table, [tabread] one index) before changing the wiring.
- tests/test_wavetables.py pins the recorded patch and the unrouted-sinesum shape as clean+silent, checks all three prescribed routes PASS the p_14 spec in the sandbox, checks the read-back distinguishes empty (0) from filled (~1, index 129 for sinesum), and checks the prompt rule and skill text.

## After (AC #3)

Same command with the skill served (SKILLS_ACTIVATION=off, i.e. the working tree): **6/6 pass**, 100% clean, $0.0046/answer vs $0.0124, p50 10.1 s vs 31.1 s, all six use '; wave sinesum 512 1' + [table wave 515] + [tabosc4~ wave] (.tmp/t528/after_p14.json).

## Ablation: which part of the fix does the work

Prompt rule only (skill not activated, so not served): p_14 x6 = **3/6** (.tmp/t528/rule_only_p14.json); failures still fill-side (two unrouted sinesum messages; one until loop clean but no 220 Hz). Skill + rule: **6/6**. The skill carries most of the gain; the rule is kept because it is general and doubled the rate on its own.

## Skill gate

`python -m evaluation.skills --skills wavetables --gate` on version 0cf6f136b95d: pass, faithfulness 4.25, relevance 5.00, citation 4.25, task success 4/4, load rate 75% (floors 4.0 / 4.5 / 4.0 / 50%).

## Aggregates (AC #4)

Patch eval, full, `python -m evaluation.patch_eval --trials 3` after the change (skill activated): without execution **75%** (published 73%), with execution **92%** (published 92%); p_14 3/3 and 3/3 (published 0/3, 2/3). Recorded in docs/PATCH_EVAL.md under 'Wavetables (TASK-5.28)' (.tmp/t528/after_patch_eval.json).

30-entry doc eval, `python -m evaluation.evaluate --generation --mode agent --sample 30 --trials 2`, before (HEAD b9d005f) vs after:
- faithfulness 4.71 ± 0.16 -> 4.80 ± 0.10
- relevance 4.93 ± 0.09 -> 4.88 ± 0.02
- citation 4.65 ± 0.07 -> 4.53 ± 0.00
- task success 93% ± 5% -> 87% ± 5%; claim checks 0 flagged in both
The wavetables skill was loaded on 0 of the 60 after-answers, and the execution rule does not reach this arm (no execution), so the only prompt difference these answers see is one more skill name in the load_skill tool. Per-entry citation changes go both ways on unrelated entries (q_014 4,4 -> 2,5; q_023 4,4 -> 5,5; q_017 5,5 -> 5,2), and those swings are as large within one arm's two trials as between arms. Read as no regression attributable to this change; every mean stays above the gate floors (4.0 / 4.5 / 4.0). With only 2 trials, a 0.1 shift in citation is not resolvable either way.

## Validation

- Skill activated from the passing gate result (skills/activation.json, skills/.activated/wavetables/0cf6f136b95d...), docs/SKILLS.md re-rendered.
- Full suite: 856 passed, 17 skipped (tests/test_wavetables.py: 12 new tests, sandbox ones included; test_skill_golden EXPECTED_SKILLS updated).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The agent's silent p_14 wavetable patches were all fill-side errors. The read chain was never at fault. The recorded patch had no wire from [until] to [f], and it sent [until] a 0, which means zero iterations. Fixing both made the same patch pass the 220 Hz spec unchanged, and a [tabread]->[print] read-back showed the table going from 0 to ~1. A 6-run control at HEAD passed 1/6, with 5 silent: a loop whose count went to [until]'s stop inlet, three [sinesum ...( messages sent nowhere (no ';' prefix), and one patch with no table at all. The fix has two parts. The new wavetables skill puts the documented sinesum + [tabosc4~] route first, gives the counter loop's rules for shapes sinesum cannot make, and says to check the read-back before rewiring. A general execution-prompt rule says to print suspect values when a clean run is silent. After the change p_14 passes 6/6 (rule alone: 3/6), at /bin/zsh.0046 vs /bin/zsh.0124 per answer. The full patch eval is 75%/92% vs the published 73%/92%, with p_14 at 3/3 in both arms. The skill gate passed (F 4.25, R 5.0, C 4.25, load 75%) and the skill is activated. The 30-entry doc eval moved within trial noise and the skill never loaded there. tests/test_wavetables.py pins the failing shapes and every prescribed route in the sandbox. Suite: 856 passed, 17 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
