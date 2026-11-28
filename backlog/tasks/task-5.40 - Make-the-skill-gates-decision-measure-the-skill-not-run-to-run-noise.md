---
id: TASK-5.40
title: 'Make the skill gate''s decision measure the skill, not run-to-run noise'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-28 01:44'
updated_date: '2026-11-28 02:46'
labels:
  - agent
  - skills
  - eval
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 51000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The per-skill golden slices have 4-5 questions and the gate scores one trial, so the decision flips between runs: in the local preview and CI run 46093371333 on the same versions, abstractions-and-dollar-zero and wavetables went pass->fail and pd-flavors fail->pass. Across both runs a single question's judge score has a standard deviation of ~1.0 (the same question scored 2 and 5), and load_skill was called for one question in one run and not in the other. A mean over n judged answers then has an error of ~1/sqrt(n): about +/-0.45 at n=5, wider than the margins the gate tests. Found while doing TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each skill's golden set has at least 15 questions, written against what its SKILL.md teaches and answerable from the indexed documentation
- [x] #2 evaluation.skills can run a slice for several trials and gates on the pooled answers (scores, load rate, task success), with the trial count recorded in the activation evidence
- [x] #3 skills.yml gates with 3 trials, and its stated cost per run is updated
- [x] #4 The chosen sizes are justified from measured per-question spread in skills/README.md
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. evaluation.skills: --trials N runs the slice N times and gates on the pooled answers; per_trial means and trials recorded in metrics.
2. Raise MAX_QUESTIONS; write 10-11 more questions per skill (15 each) from each SKILL.md, checked against the index so they are answerable.
3. skills.yml: --trials 3; cost stated.
4. skills/README.md: sizing from the measured spread.
5. Tests; then dispatch the CI gate (TASK-5.37).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
evaluation.skills --trials N answers each question N times and pools every answer (scores, load rate, task success); questions/trials/answers are recorded in the activation metrics and faithfulness_by_trial in the result. Golden sets grown to 15 (16 for granular-synthesis): 61 new questions written from each SKILL.md; each was retrieved against the index and pd-file-011 was replaced because it retrieves nothing (an unretrievable question is a gate miss whatever the skill does). GATED_QUESTIONS = 15 is enforced for shipped skills; MAX_QUESTIONS raised to 25. skills.yml gates with --trials 3, timeout 60 min, cost ~$0.013/answer => ~$0.60 per skill, ~$3.60 for all six. Sizing justified in skills/README.md from the measured per-answer sd ~1.0. Growing golden.json changes every skill's digest, so all six need the new gate run.
Validation: tests/test_skill_activation.py::TestPooledTrials, tests/test_skill_golden.py (15-question floor), tests/test_ci_workflows.py::test_the_skill_gate_pools_three_trials; non-sandbox suite 874 passed.

CI run 46095333752 (all six, 15+ x 3): granular-synthesis passed (faithfulness 4.15, relevance 4.96, citation >= 4, load 56%) but its push was rejected because main moved; fixed in b025b94 (cherry-pick the activation onto the tip, 3 attempts). Re-gate 46098822877 of granular-synthesis alone: faithfulness 4.10, relevance 4.85, load 52%, citation 3.98 -> fail by 0.02. The two runs agree to within ~0.1 on every metric (the old 5x1 runs disagreed by up to 1.6), so the decision is now stable and granular-synthesis sits on the citation line. Other results (46095333752): abstractions-and-dollar-zero load 44%; dsp-debugging citation 3.38, load 47%; pd-flavors new version relevance 4.38 (its earlier CI-gated version keeps serving); wavetables faithfulness 3.82; working-from-a-pd-file load 9%.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The skill gate's decision now reflects the skill: every shipped skill has 15+ golden questions, evaluation.skills --trials pools answers across trials, and skills.yml gates with 3 trials (~$0.60 per skill). Two CI runs of granular-synthesis agreed within ~0.1 on every metric, against differences of up to 1.6 under the old 5x1 slices. Also fixed the activation push being lost when main moves during a run.
<!-- SECTION:FINAL_SUMMARY:END -->
