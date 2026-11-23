---
id: TASK-3
title: Gate generation quality in CI
status: Done
assignee:
  - '@deepseek'
created_date: '2026-11-23 20:39'
updated_date: '2026-11-23 22:50'
labels:
  - evaluation
  - ci
dependencies: []
references:
  - .github/workflows/eval.yml
  - evaluation/generation.py
  - evaluation/judge.py
priority: medium
type: enhancement
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Retrieval metrics are gated in CI (`tests/` threshold assertions), but the LLM-as-judge generation eval (faithfulness, answer relevance, citation correctness) only runs on manual `workflow_dispatch`. A prompt change that hurts answer quality can merge without anyone noticing. Run a small, cost-bounded sample on PRs and fail on regressions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Pull requests that touch prompts, `rag.py` or `evaluation/` run a sampled generation eval automatically
- [x] #2 The build fails when faithfulness or relevance falls below documented thresholds
- [x] #3 Per-run cost is bounded (sample size and model documented) and reported in the job summary
- [x] #4 The published eval report shows the latest generation results alongside the retrieval tables
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Put the gate policy in a dependency-free evaluation/gates.py: documented thresholds, check_generation_thresholds() returning one message per failing dimension, and the job-summary markdown. Keep it free of rag/chroma/API imports so it is unit-testable without keys.
2. Measure judge cost instead of estimating it: thread an optional usage_sink through judge._score_single -> evaluate_generation -> compare_models, and aggregate per-model judge tokens into avg_judge_cost_usd / avg_total_cost_usd / run_total_cost_usd in run_generation_eval.
3. Extend evaluation.evaluate with --gate (non-zero exit below threshold) and --summary PATH (append markdown stating sample, models, measured cost and verdict, so it works with $GITHUB_STEP_SUMMARY). Return an exit code from main() and print results only when not --quiet.
4. Publish generation numbers in docs/EVAL.md: eval.yml runs --all --sample 10 on main/dispatch with --report and --gate; PRs stay retrieval-only.
5. New .github/workflows/generation-eval.yml: pull_request paths rag.py, evaluation/**, itself; runs the 10-entry eval with --gate --summary, cancels superseded runs, 20-minute timeout.
6. Set thresholds from measured baselines across three runs (faithfulness 4.7-5.0, relevance 5.0, citation 4.6): gate faithfulness >= 4.0 and relevance >= 4.5, report citation without gating. Document the numbers, cost bound and rationale in README and in the generated report footer.
7. Tests: threshold pass/fail/boundary/missing-score, multi-trial summary shape, job-summary content, report includes retrieval + generation, gate exit codes, append semantics.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-11-23:
- evaluation/gates.py: GENERATION_THRESHOLDS {faithfulness: 4.0, relevance: 4.5}, check_generation_thresholds(), generation_summary_markdown(), plus mean_score() which tolerates the --trials '*_mean_avg' key shape. No rag/chroma imports, so the policy is testable without keys.
- Judge spend is measured, not estimated: an optional usage_sink threads through _score_single -> evaluate_generation -> compare_models (default None, so the inline production faithfulness check is untouched). run_generation_eval turns that into avg_judge_cost_usd, avg_total_cost_usd and run_total_cost_usd next to the existing generation cost fields.
- evaluation.evaluate: --gate exits 1 below threshold (2 if --gate is used without a generation run), --summary appends markdown for $GITHUB_STEP_SUMMARY, and main() now returns an exit code. Results are printed only when not --quiet, which also stops --quiet CI runs dumping the full JSON into the log.
- Reports: docs/EVAL.md now carries the generation table (faithfulness/relevance/citation, n, cost/query) next to the retrieval tables, with the generation gate documented in the footer.
- CI: eval.yml runs --all --sample 10 with --report and --gate on main/dispatch (PRs stay retrieval-only); new generation-eval.yml runs the 10-entry gate on PRs touching rag.py, evaluation/** or itself.

Baseline and threshold rationale: three real sampled runs on the first 10 entries gave faithfulness 4.8, 4.9, 4.7 and relevance 5.0, 5.0, 5.0 (citation 4.6, 4.6, 4.9). Faithfulness moved 0.1 between identical runs, so the gate sits ~0.7 below the lowest observed faithfulness mean and 0.5 below relevance. Gate selection is 'fail on a real regression', not 'alert on judge noise'. Citation correctness is excluded because the same sample produced a minimum of 2 with a mean of 4.6.

Cost: measured by wrapping the Anthropic client for a full 10-entry run — generation $0.2187 + judge $0.0908 = $0.3094, i.e. ~$0.031/entry. The harness now reports this itself; the three implementation runs came out at $0.2894-$0.2942. The workflow comment and README document ~$0.32 per PR run.

Verification:
- tests/test_generation_gate.py: 19 passed (pass/fail/boundary, missing score, missing per_model, citation not gated, custom thresholds, multi-trial shape, summary markdown content, gate exit codes 0/1/2, report carries both tables + thresholds + cost, summary append semantics).
- Full suite: 95 passed in 113s (includes the 4 retrieval threshold tests).
- Real runs: 'python -m evaluation.evaluate --all --sample 10 --report docs/EVAL.md --gate --summary /tmp/summary.md --quiet' exited 0 with faithfulness 4.70 / relevance 5.00 / citation 4.90 and $0.2894 measured cost; the committed docs/EVAL.md is that run. '--generation --dataset <empty> --gate' exited 1 with 'no entries were scored', proving the process-level failure path.
- Both workflow YAMLs parse; the command generation-eval.yml runs was exercised locally against the same keys.

Caveat: the gate cannot run on fork PRs (no repository secrets); the README says so. GitHub-side execution of the new workflow itself is unverified until the next push.

CI verification 2026-11-23 (pushed main 3fbfbd7, rebased over the earlier CI EVAL.md commit 8e0611d):
- Evaluation run 45281719435 (push, 6m10s): success. Every step green — index build, full eval (retrieval + sampled generation), commit updated EVAL.md, pytest. The PR-only retrieval step correctly skipped. CI wrote cb280bc 'ci: update evaluation results [skip ci]', whose docs/EVAL.md now carries the generation table: faithfulness 4.70, relevance 5.00, citation 4.80, n=10, $0.0299/query, alongside retrieval (recall@5 80.00%, MRR 0.736) and both threshold footers. AC #4 is now proven by the real pipeline rather than a local run.
- Generation eval gate run 45282777149 (workflow_dispatch, 3m49s): success. This was the new workflow's first execution on GitHub, so its triggers, secrets, index build and gated command are no longer untested; the PR path filter itself stays declarative until a PR opens.
- Railway deployed cb280bc to production at 22:43:51Z ('Deployment has completed') and https://pd-chatbot.vercel.app/api/health returns {"status":"ok"}. No runtime code changed in this task, so the deploy is content-equivalent for the app.
- Both runs emit a Node 20 deprecation annotation (actions/checkout@v4 and actions/setup-python@v5 forced onto Node 24). Upstream action drift, not caused by this change; bumping to newer action majors would clear it.
- Cost of the two verification runs: roughly $0.30 + $0.30. This note is committed locally and left unpushed on purpose, since every push to main re-runs the full sampled generation eval.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Generation quality is now gated in CI instead of only measured on demand. A new workflow (generation-eval.yml) runs a 10-entry sampled eval on pull requests that touch rag.py (home of SYSTEM_PROMPT), evaluation/** (judge rubric, dataset, harness) or the workflow itself, and fails the build when the mean faithfulness or relevance falls below the documented thresholds — faithfulness >= 4.0, relevance >= 4.5 on the 1-5 judge scale. Citation correctness is reported but not gated because it is the noisiest dimension.

The policy lives in evaluation/gates.py (dependency-free, so it is unit-testable without keys) together with the job-summary builder. evaluation.evaluate gained --gate (non-zero exit; 2 when used without a generation run) and --summary, which appends a markdown block to $GITHUB_STEP_SUMMARY stating the sample size, both models, the measured dollars for the run and the verdict. Cost is measured rather than estimated: judge token usage is now threaded out of judge.py through an optional usage sink and aggregated per model, so a 10-entry run reports ~$0.29-$0.31 (about $0.031/query, generation $0.022 + judge $0.009).

On main and workflow_dispatch, eval.yml runs the full eval (--all --sample 10) and writes the generation table into docs/EVAL.md next to the retrieval tables, with the gate documented in the footer; the committed report is from that command.

Thresholds were set from measurement, not guesswork: three sampled runs gave faithfulness 4.8/4.9/4.7 and relevance 5.0/5.0/5.0, so the gate sits below the observed noise band and fails on genuine regressions.

Verified by 19 new tests in tests/test_generation_gate.py (threshold boundaries, missing scores, citation-not-gated, multi-trial key shapes, summary content, exit codes 0/1/2, report content, append semantics) and a 95-test full suite; a real gated run exited 0 with faithfulness 4.70/relevance 5.00, while a no-scores run exited 1; both workflows parse and their commands were exercised locally. Unverified until the next push: GitHub actually executing the new workflow, and fork PRs, which have no secrets and so cannot run the gate (documented in the README).
<!-- SECTION:FINAL_SUMMARY:END -->
