---
id: TASK-5.7
title: Add agent-level eval metrics
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
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
