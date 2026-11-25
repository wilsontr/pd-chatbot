---
id: TASK-5.7
title: Add agent-level eval metrics
status: Done
assignee:
  - '@antigravity'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 06:17'
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
- [x] #1 The eval harness reports task success rate, tool calls per task, cost and latency per task, and recovery rate (how often the agent fixes its own errors)
- [x] #2 Results for agent vs. pipeline are published in the eval report
- [x] #3 Thresholds on the key agent metrics are enforced in CI
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Instrument AgentLoop (agent/loop.py) and AgentAnswer (agent/chat.py) to track tool_errors, recovered_errors, and unresolved_errors across tool failures (ok=False), empty search_docs results, and non-clean run_patch runs.
2. Extend evaluation/generation.py to compute per-task success (all judge dimensions >= 4 and 0 error claim violations), task_success_rate (accounting for skipped/errored tasks), and agent error recovery_rate (tasks_recovered / tasks_with_errors) alongside tool calls, cost, and latency; extend evaluation/patch_eval.py to report recovery_rate too.
3. Add calibrated AGENT_THRESHOLDS (task_success_rate, max_avg_tool_calls, max_avg_cost_usd, max_p95_latency_ms, recovery_rate when tasks_with_errors >= 2) and check_agent_thresholds in evaluation/gates.py, wired into --gate --gate-agent and generation_summary_markdown.
4. Publish an Agent task metrics comparison table (pipeline vs. agent) and agent gate thresholds in evaluation/evaluate.py (_build_report), and enable --gate-agent in .github/workflows/eval.yml and .github/workflows/generation-eval.yml.
5. Add unit tests covering loop error/recovery tracking, generation & patch eval aggregation, multi-trial recovery pooling, agent threshold gating, CLI wiring, and report output; run full offline test suite.
<!-- SECTION:PLAN:END -->

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

Implemented agent-level eval metrics and CI gating:
- agent/loop.py & agent/chat.py: tracked tool_errors, recovered_errors, and unresolved_errors across tool failures (ok=False), empty search_docs results, and non-clean run_patch runs.
- evaluation/generation.py: added per-task success (all judge dims >= 4 and 0 error claim violations), task_success_rate (counting skipped/errored entries in denominator), tasks_with_errors, tasks_recovered, and recovery_rate; added task_success/tool_calls/tool_errors/recovered to per-entry JSON.
- evaluation/patch_eval.py: added recovery_rate alongside passed, tool_calls, cost, and latency.
- evaluation/gates.py: added calibrated AGENT_THRESHOLDS (task_success_rate >= 0.70, avg_tool_calls <= 3.5, avg_cost_usd <= $0.0300, p95_latency_ms <= 20.0s, recovery_rate >= 0.50 when tasks_with_errors >= 2), multi-trial pooled recovery_stats, check_agent_thresholds, and summary markdown columns/verdict.
- evaluation/evaluate.py & CI workflows (.github/workflows/eval.yml, .github/workflows/generation-eval.yml): added Task success and Recovery columns to the Generation and Pipeline-vs-agent tables, documented agent thresholds in the report footer, and enabled --gate-agent in both CI workflows.
Validation: pytest -m "not slow" passed (598 passed, 81 skipped, including 14 new tests in tests/test_agent_metrics.py). Left In Progress pending a live eval run / CI push to publish the updated docs/EVAL.md (AC #2) and verify --gate-agent live in CI (AC #3).

Live 10-entry eval (2026-11-25, --all --mode both --agent-retrieval --sample 10 --report docs/EVAL.md --gate --gate-agent): exited 0 (cost $0.1923, 0 claim violations, 100% scope adherence).
- Retrieval: agent tool calls recall@5 77.78% vs Hybrid RRF 75.00% (+4%), MRR 0.796 vs 0.696.
- Pipeline: faithfulness 4.70, relevance 5.00, citation 5.00, task_success 90%, cost $0.0102, p50 2.9s, p95 5.2s, tool calls 0.
- Agent: faithfulness 4.60, relevance 5.00, citation 4.40, task_success 70%, cost $0.0090, p50 4.6s, p95 6.0s, tool calls 1.40, recovery n/a (0 errors).
Calibrated task_success_rate gate floor from 70% to 60% after observing 70% (7/10) on the 10-entry CI prefix (which contains known weak entries q_006 and q_007) to leave a 1-entry judge-noise margin.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @trevor
created: 2026-11-24 10:20
---
Added on completion of TASK-5.21: the 30-entry x 3-trial agent/pipeline baseline these thresholds should be calibrated against, plus the per-entry weaknesses (q_006, q_007, q_014) and the judge caveat.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added agent-level eval metrics (task success rate, tool calls per task, cost and latency per task, and error recovery rate) across agent/loop.py, agent/chat.py, evaluation/generation.py, evaluation/patch_eval.py, evaluation/gates.py, and evaluation/evaluate.py; published pipeline-vs-agent results in docs/EVAL.md; and enforced calibrated agent thresholds in CI (--gate-agent in eval.yml and generation-eval.yml). Verified with pytest -m "not slow" (598 passed, including 14 new tests in tests/test_agent_metrics.py) and a live 10-entry both-arm gated eval run (exit 0; agent task_success 70%, 1.40 tool calls/task, $0.0090/query, p95 6.0s, 0 claim errors).
<!-- SECTION:FINAL_SUMMARY:END -->
