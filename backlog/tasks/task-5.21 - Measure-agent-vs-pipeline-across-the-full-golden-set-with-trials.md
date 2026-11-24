---
id: TASK-5.21
title: Measure agent vs pipeline across the full golden set with trials
status: To Do
assignee: []
created_date: '2026-11-24 09:09'
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
- [ ] #1 The eval report carries 30-entry, at-least-3-trial means and standard deviations for both arms on faithfulness, relevance and citation, produced by a recorded command
- [ ] #2 The retrieval comparison at 30 entries includes the agent tool-call strategy and the vector/BM25 ablations, with recall@k, MRR and nDCG
- [ ] #3 Cost and latency (p50 and p95) are reported per arm, and the run total dollar cost is recorded on the task
- [ ] #4 `docs/EVAL.md` and the measured paragraph in `README.md` state the outcome, including whether the TASK-5.1 parity conclusion holds
- [ ] #5 The `--trials` + `--mode both` path is exercised end to end; any defect found in trial aggregation or the report renderer is fixed with a unit test
<!-- AC:END -->
