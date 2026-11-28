---
id: TASK-5.37
title: Gate the baseline skills and check activation on main
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-28 02:48'
labels:
  - agent
  - skills
  - eval
dependencies:
  - TASK-5.40
  - TASK-5.41
  - TASK-5.42
  - TASK-5.43
  - TASK-5.44
  - TASK-5.45
references:
  - skills/activation.json
  - .github/workflows/skills.yml
  - evaluation/skills.py
parent_task_id: TASK-5
priority: medium
type: task
ordinal: 47000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Five of the six skills (abstractions-and-dollar-zero, dsp-debugging, granular-synthesis, pd-flavors, working-from-a-pd-file) are pinned in skills/activation.json as "baseline" and have never passed their own golden-set gate. The only gated skill, wavetables (TASK-5.28), was activated from a local run (actor "local"). skills.yml runs only on pull requests, so a direct push to main changes what is served with no CI check. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every activated skill has gated evidence recorded in skills/activation.json, or is deactivated with a recorded reason
- [ ] #2 wavetables is re-gated in CI and its evidence replaces the local one
- [x] #3 A check on main fails when skills/activation.json pins a version that has no CI-gated evidence or does not match its snapshot
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Record the GitHub Actions run (metrics.ci) on every CI activation.
2. Add 'evaluation.skills --verify' (fails on a pin without Skill-eval-gate CI evidence, or a missing/mismatched snapshot) and a skills-check.yml job on main and PRs.
3. Add '--deactivate NAME --reason' that drops the pin and records the reason under 'deactivated' in the manifest and docs/SKILLS.md.
4. Let a manual skills.yml run commit its activations to the branch it ran on (main), keeping skills that passed even when another failed.
5. After the push: dispatch skills.yml for every skill on main; deactivate any skill that fails, with its reason (user decision).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Steps 1-4 implemented and tested (tests/test_skill_activation.py TestCiEvidence/TestVerify/TestDeactivate, tests/test_ci_workflows.py::TestSkillsCheck). 'python -m evaluation.skills --verify' on the current manifest fails on all six pins: five baselines and wavetables (activated by local).
Local preview of the five baseline slices (not activated, not evidence): abstractions-and-dollar-zero passes; dsp-debugging fails load rate 40% < 50%; granular-synthesis fails faithfulness 3.40 and citation 3.20 < 4; pd-flavors fails relevance 4.00 < 4.5; working-from-a-pd-file fails relevance 4.00 < 4.5. Slices are 4-5 questions, so a CI run may differ. Deactivating the failing skills changes what production serves, so that is left to the user.
Known gap: a squash merge whose message includes the bot's '[skip ci]' line skips the push-to-main check.

CI gate run 46093371333 (workflow_dispatch on main, db48672, all six skills): pd-flavors passed and was recorded with CI evidence (commit 4638af2). Failed: abstractions-and-dollar-zero (load 40%), dsp-debugging (faithfulness 3.80, citation 3.20), granular-synthesis (relevance 4.40), wavetables (faithfulness 3.00), working-from-a-pd-file (load 0%). Compared with the local preview, abstractions-and-dollar-zero and wavetables flipped pass->fail and pd-flavors fail->pass: on 4-5 question slices the gate decision is mostly noise. --verify on main now fails on the five remaining pins. AC1 and AC2 stay open pending the user's decision (deactivate, or enlarge the slices / run more trials first).

After TASK-5.40 (15+ x 3 trials, runs 46095333752 and 46098822877): only pd-flavors (old version) has CI evidence. The failures are now stable measurements, not noise: working-from-a-pd-file is almost never loaded (9%), abstractions-and-dollar-zero and dsp-debugging load just under 50%, dsp-debugging citation 3.38, wavetables faithfulness 3.82, granular-synthesis citation 3.98 (passed at >= 4 in the previous run). skills-check still fails on main. Next step is the user's: improve the failing skills, or deactivate them.
<!-- SECTION:NOTES:END -->
