---
id: TASK-9
title: Recover citation quality before downgrading the answer model
status: To Do
assignee: []
created_date: '2026-11-24 01:36'
labels:
  - evaluation
  - quality
dependencies: []
references:
  - docs/MODEL_COMPARISON.md
  - rag.py
  - evaluation/judge.py
  - evaluation/gates.py
priority: medium
type: enhancement
ordinal: 28000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The 2026-11-23 model comparison (docs/MODEL_COMPARISON.md) showed that gated quality is tied across five models but citation correctness is not: `claude-sonnet-4-6` scores 4.65 while `claude-haiku-5-5`, `deepseek-flash`, `claude-sonnet-5-5` and `deepseek-v4-pro` land at 4.37-4.51. Switching to `claude-haiku-5-5` would cut production cost from $27.40 to $4.47 per 1000 queries (84%), but it also drops citation quality in a product whose value is grounded, cited answers. The decision was to keep `claude-sonnet-4-6` and try to recover citations first. The two levers are the citation instruction in `SYSTEM_PROMPT` (rag.py) and the answer format the model is asked for; the judge rubric in evaluation/judge.py already penalises missing or unverifiable URLs. Latency was also unmeasured in the comparison and should be captured before any switch, since the cheaper models write roughly twice the output tokens.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 At least one prompt variant is measured with the comparison protocol (all 30 entries, 2 trials, claude-haiku-4-5 judge) and lifts claude-haiku-5-5 citation correctness to within 0.1 of the 4.65 sonnet-4-6 baseline, without faithfulness dropping below 4.0 or relevance below 4.5
- [ ] #2 Citation correctness is either gated in CI with a documented threshold, or explicitly left ungated with the rationale recorded in evaluation/gates.py and the README
- [ ] #3 The re-measurement commits its raw results (JSON) and updates docs/MODEL_COMPARISON.md with the new numbers, the date and the price snapshot, so it is comparable to the 2026-11-23 baseline
- [ ] #4 End-to-end latency and time-to-first-token are measured for sonnet-4-6 and the candidate at the same sample size, so a switch decision is not made on quality and cost alone
- [ ] #5 The final keep-or-switch decision is recorded with quality, citation, cost per 1000 queries and latency deltas; if citations do not recover, that is recorded and the switch is explicitly deferred rather than left ambiguous
<!-- AC:END -->
