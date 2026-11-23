---
id: TASK-3
title: Gate generation quality in CI
status: To Do
assignee: []
created_date: '2026-11-23 20:39'
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
- [ ] #1 Pull requests that touch prompts, `rag.py` or `evaluation/` run a sampled generation eval automatically
- [ ] #2 The build fails when faithfulness or relevance falls below documented thresholds
- [ ] #3 Per-run cost is bounded (sample size and model documented) and reported in the job summary
- [ ] #4 The published eval report shows the latest generation results alongside the retrieval tables
<!-- AC:END -->
