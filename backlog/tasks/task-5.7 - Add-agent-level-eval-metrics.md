---
id: TASK-5.7
title: Add agent-level eval metrics
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
updated_date: '2026-11-24 10:20'
labels:
  - agent
dependencies:
  - TASK-5.1
references:
  - evaluation
parent_task_id: TASK-5
priority: high
type: feature
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
RAG metrics alone do not show whether an agent is good. Measure task-level behaviour so changes to tools, prompts and skills can be compared.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The eval harness reports task success rate, tool calls per task, cost and latency per task, and recovery rate (how often the agent fixes its own errors)
- [ ] #2 Results for agent vs. pipeline are published in the eval report
- [ ] #3 Thresholds on the key agent metrics are enforced in CI
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Baseline from TASK-5.21** (2026-11-24, 30 entries, both arms, 3 trials, judge claude-haiku-4-5; full tables in docs/EVAL.md):

| Arm | Faithfulness | Relevance | Citation | Cost/query | p50 | p95 | Tool calls |
|---|---|---|---|---|---|---|---|
| pipeline | 4.71 ± 0.16 | 4.82 ± 0.05 | 4.60 ± 0.03 | $0.0105 | 3.6 s | 5.2 s | 0 |
| agent | 4.66 ± 0.05 | 4.87 ± 0.00 | 4.50 ± 0.12 | $0.0125 | 5.7 s | 7.8 s | 1.48 |

Paired deltas (agent − pipeline): faithfulness −0.05 ± 0.19, relevance +0.05 ± 0.05, citation −0.10 ± 0.14 → parity holds. Calibrate agent thresholds against these means, not against a single trial: the trial-to-trial standard deviations above are the noise floor.

Known agent weaknesses to target with task-level metrics (not aggregate means):
- q_007 (`[pack]`/`[unpack]`): faithfulness 2/4/3 (mean 3.00) — asserts outlet ordering that the retrieved excerpts do not state; the deterministic check publishes it as a warning in 2 of 3 trials.
- q_006 and q_014 (`[snapshot~]`): faithfulness 2 — gets the signal/bang inlet arrangement wrong.
- The judge is itself unreliable on inlet-order claims (`[line~]`, q_004): it scored a reversed list example 5/5/5 and a correct left-to-right answer 2 on both arms. Don't build thresholds that depend on that rule without a rubric fix.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @trevor
created: 2026-11-24 10:20
---
Added on completion of TASK-5.21: the 30-entry x 3-trial agent/pipeline baseline these thresholds should be calibrated against, plus the per-entry weaknesses (q_006, q_007, q_014) and the judge caveat.
---
<!-- COMMENTS:END -->
