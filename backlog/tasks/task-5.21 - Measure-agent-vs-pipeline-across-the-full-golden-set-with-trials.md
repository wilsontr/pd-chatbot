---
id: TASK-5.21
title: Measure agent vs pipeline across the full golden set with trials
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-24 09:09'
updated_date: '2026-11-24 10:20'
labels:
  - agent
dependencies: []
references:
  - evaluation/generation.py
  - evaluation/evaluate.py
  - docs/EVAL.md
  - README.md
parent_task_id: TASK-5
priority: medium
type: task
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.1 published the first pipeline-vs-agent comparison, but it is a single trial of the 10-entry CI prefix and the judge is noisy: across two consecutive runs the pipeline's own faithfulness moved 5.00 -> 4.70, and the agent's tool-call recall@5 moved 80.6% -> 76.1% after a one-line prompt change. The parity conclusion ("agent answers are within noise of the pipeline") cannot rest on one 10-entry trial.

Run the comparison properly and publish it: the full 30-entry golden set, both arms, and at least 3 trials with `--trials` (the harness already merges trials into mean/std per model label). Then state what the numbers actually support:

- Whether the agent beats, matches or trails the pipeline on faithfulness, relevance and citation, with the spread across trials.
- The retrieval comparison at 30 entries: agent `search_docs` tool calls vs hybrid RRF vs the vector/BM25 ablations, on recall@k, MRR and nDCG.
- Cost and p50/p95 latency per arm, and the measured dollar cost of the run.

Update `docs/EVAL.md` and the measured paragraph in `README.md` with those numbers.

Notes and command shape:
- `python -m evaluation.evaluate --all --mode both --agent-retrieval --sample 30 --trials 3 --report docs/EVAL.md --output <json>`
- Cost scales with entries x arms x trials. Measured $0.037/entry for both arms on the 10-entry run, so budget roughly $3-4 for 30 x 2 x 3 including judge calls.
- Check that the report renders the multi-trial shape for both arms: `_aggregate_trials` suffixes numeric fields with `_avg`/`_std`, and the report reads the `_avg` variants for only some fields (cost, p50), so the Pipeline-vs-agent table may need the same treatment.
- The published 30-entry multi-trial numbers become the baseline TASK-5.7 calibrates agent thresholds against; note them there when done.
- Ordering with TASK-5.20: its fix makes the agent numbers more meaningful, so if the fix lands first, run this afterwards. The run itself does not depend on it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The eval report carries 30-entry, at-least-3-trial means and standard deviations for both arms on faithfulness, relevance and citation, produced by a recorded command
- [x] #2 The retrieval comparison at 30 entries includes the agent tool-call strategy and the vector/BM25 ablations, with recall@k, MRR and nDCG
- [x] #3 Cost and latency (p50 and p95) are reported per arm, and the run total dollar cost is recorded on the task
- [x] #4 `docs/EVAL.md` and the measured paragraph in `README.md` state the outcome, including whether the TASK-5.1 parity conclusion holds
- [x] #5 The `--trials` + `--mode both` path is exercised end to end; any defect found in trial aggregation or the report renderer is fixed with a unit test
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Fix the multi-trial report path: pipeline-vs-agent table, p50/p95, cost and retrieval rendering under _avg/_std.
2. Run the 30-entry, 3-trial, both-arm eval with --agent-retrieval, writing docs/EVAL.md and a JSON artifact.
3. Record the numbers (scores, retrieval, cost, p50/p95) on the task and in docs/EVAL.md + the README measured paragraph; state whether the TASK-5.1 parity conclusion holds.
4. Note the 30-entry multi-trial baseline on TASK-5.7.
5. Add unit tests for trial aggregation and the report renderer.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Recorded command (AC #1)

```
PYTHONPATH=. python -m evaluation.evaluate --all --mode both --agent-retrieval \
  --sample 30 --trials 3 --gate \
  --report docs/EVAL.md --output .tmp/eval_521_full.json --summary .tmp/eval_521_summary.md
```

Exit code 1 on the last run only because of a claim-check false positive (a sentence about MAX's ordering on q_011) that the checker now excludes; re-checking the recorded results with the final checker gives no threshold, scope or claim failures. The report was regenerated from the recorded JSON with the final renderer (`_build_report`), and the two answer-only claim checks were recomputed from the stored answers after that fix — the scores are untouched.

The run is also reproducible from CI: `eval.yml` now has a `trials` dispatch input (`sample=30`, `trials=3`) and a timeout that follows it.

## Generation (AC #1)

30 entries, both arms, 3 trials, judge `claude-haiku-4-5`; cells are mean ± std across trials:

| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 | p95 | Tool calls |
|---|---|---|---|---|---|---|---|---|
| pipeline | 4.71 ± 0.16 | 4.82 ± 0.05 | 4.60 ± 0.03 | 30 | $0.0105 | 3.6 s | 5.2 s | 0 |
| agent | 4.66 ± 0.05 | 4.87 ± 0.00 | 4.50 ± 0.12 | 30 | $0.0125 | 5.7 s | 7.8 s | 1.48 |

Paired per-trial differences (agent − pipeline): faithfulness **−0.05 ± 0.19**, relevance **+0.05 ± 0.05**, citation **−0.10 ± 0.14**. None is larger than its own spread and at least 0.1, so the report's verdict line reads **"the agent matches the pipeline"**: the TASK-5.1 parity conclusion holds at 30 entries x 3 trials.

## Retrieval at 30 entries (AC #2)

| Strategy | recall@3 | recall@5 | recall@10 | MRR | nDCG@5 | nDCG@10 |
|---|---|---|---|---|---|---|
| Hybrid RRF | 73.89% | 75.00% | 75.00% | 0.696 | 0.692 | 0.692 |
| Vector-only | 62.78% | 72.78% | 73.89% | 0.611 | 0.634 | 0.639 |
| BM25-only | 28.33% | 36.67% | 40.00% | 0.265 | 0.283 | 0.293 |
| Agent tool calls | 72.78% | 79.44% | 82.78% | 0.781 | 0.752 | 0.822 |

The agent's tool-call retrieval beats the pipeline's fixed search at 30 entries (recall@5 +4.4 points, MRR +0.085, nDCG@5 +0.060) with 1.48 tool calls per question. Hybrid still beats both ablations on recall@5 (+3% over vector, +105% over BM25).

## Cost and latency (AC #3)

- Measured generation + judge cost for the whole run: **$2.0712** (2 arms x 30 entries x 3 trials; retrieval embedding calls are extra). Per query: $0.0105 pipeline, $0.0125 agent.
- p50 / p95 per arm are in the table above; the agent is +2.1 s p50 and +2.6 s p95 (one extra model turn on average).
- Session total for this task while iterating (multiple seeded runs, A/B controls and the re-run after the claim-check false-positive fix): ~$4.6 measured, all recorded in the run summaries.

## Docs (AC #4)

- `docs/EVAL.md`: 30-entry, 3-trial report with mean ± std, the Pipeline-vs-agent table, the deltas, the generated verdict line, the deterministic claim-check section, p50/p95, cost and the run total.
- `README.md`: the measured paragraph now quotes the 30-entry 3-trial table, states that the parity conclusion holds, gives the retrieval comparison and the cost/latency, and names the two measured caveats (judge unreliability on ordering claims; the agent's remaining unsupported specifics).

## Defects fixed on the `--trials` path (AC #5)

- `_aggregate_trials` dropped every non-numeric field, so `mode` was lost and a multi-trial run rendered no Tool-calls column and no agent-arm dispatch; it now carries non-numeric metadata from the first trial (test: `test_the_arm_label_is_preserved`).
- A trial that skips an entry (no chunks retrieved) produced `n` from the first trial only; `n` is now the mean with `n_by_trial` kept (test: `test_a_skipped_entry_in_one_trial_reports_the_mean_count`).
- The merged summary lost every per-entry record, so the claim section and per-entry auditing went blank on multi-trial runs; `entries` are now flattened with a `trial` number (test: `test_entries_are_flattened_with_their_trial_number`).
- The report had no spread and no p95; scores now render mean ± std, p50 and p95 are columns, and the run total cost is a line under the table (tests in `tests/test_eval_trials.py`).
- The job summary read `run_total_cost_usd` (absent on a merged summary, so it printed nothing) and had no trials or latency context; it now reads `_avg`, states the trial count and multiplies by trials (test: `test_summary_states_trials_and_the_total_cost`).
- The report had no verifiable "does parity hold" statement; `_parity_reading` derives it from the paired per-trial deltas (tests for matches / beats / trails).

## Findings worth carrying forward

- **The judge is wrong about Pd inlet order in both directions.** It scored a reversed `[line~]` list example 5/5/5, and scored correct left-to-right answers 2 on both arms (pipeline q_004 trial 3, agent A/B run 2). It affects both arms, so the comparison stays fair, but it inflates faithfulness variance and makes single-trial per-entry scores unusable. A rubric clarification naming the documented left-to-right distribution is the obvious next fix; it needs its own re-measurement, so it is not in this task (scope).
- **The agent's remaining quality gap is unsupported specifics**, not the TASK-5.1 claim: `[snapshot~]` inlet arrangement (q_006, q_014) and `[unpack]` outlet order (q_007: faithfulness 2/4/3, mean 3.00; the checker publishes the outlet-order warning in 2 of 3 trials). Both are recorded for TASK-5.7's calibration.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Published the full agent-vs-pipeline comparison and fixed the multi-trial path.

**Run.** `python -m evaluation.evaluate --all --mode both --agent-retrieval --sample 30 --trials 3 --gate ...` (also reproducible from CI via the new `trials` dispatch input). 30 entries, 3 trials, both arms: pipeline 4.71 ± 0.16 / 4.82 ± 0.05 / 4.60 ± 0.03; agent 4.66 ± 0.05 / 4.87 ± 0.00 / 4.50 ± 0.12 (faithfulness / relevance / citation). Paired deltas −0.05 ± 0.19, +0.05 ± 0.05, −0.10 ± 0.14 → **parity holds**; the report prints that verdict from the numbers. Retrieval at 30 entries: agent tool calls 79.4% recall@5 / 0.781 MRR / 0.752 nDCG@5 vs hybrid 75.0% / 0.696 / 0.692, with vector and BM25 ablations in the table. Cost bash.0105 (pipeline) / bash.0125 (agent) per query, p50 3.6 s / 5.7 s, p95 5.2 s / 7.8 s; measured run total .0712.

**Renderer fixes (with tests in `tests/test_eval_trials.py`).** `_aggregate_trials` no longer drops non-numeric metadata (it lost `mode`, which silently removed the agent columns), reports the mean `n` when a trial skipped an entry, and flattens per-entry records so the claim section survives trials. The report now shows mean ± std, p50/p95, the run total, nDCG@10, and a generated parity verdict; the job summary reads `_avg` for the run cost (it printed nothing on multi-trial runs) and states the trial count.

**Docs.** `docs/EVAL.md` regenerated from the recorded JSON with the final renderer; `README.md`'s measured paragraph replaced with the 30-entry 3-trial table, the retrieval comparison, the cost/latency, and the two measured caveats. 276 tests passing.
<!-- SECTION:FINAL_SUMMARY:END -->
