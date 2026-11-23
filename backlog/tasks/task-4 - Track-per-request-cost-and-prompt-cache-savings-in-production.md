---
id: TASK-4
title: Track per-request cost and prompt-cache savings in production
status: To Do
assignee: []
created_date: '2026-11-23 20:39'
labels:
  - observability
dependencies: []
references:
  - rag.py
  - evaluation/generation.py
priority: low
type: enhancement
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Langfuse traces in `rag.py` already record token usage (including cache reads), and `evaluation/generation.py` has a `_cost_usd` helper, but production requests do not report dollar cost and nobody can see how much prompt caching saves. Quantifying that is a core LLM-ops story.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each production trace records the cost in USD for every model call (classifier, history compression, generation, guardrail checks) and a total for the request
- [ ] #2 Cache-read savings are calculated per request (actual cost vs. cost without caching)
- [ ] #3 One pricing table is shared by production and the eval harness
- [ ] #4 The README reports observed average cost per request and cache savings
<!-- AC:END -->
