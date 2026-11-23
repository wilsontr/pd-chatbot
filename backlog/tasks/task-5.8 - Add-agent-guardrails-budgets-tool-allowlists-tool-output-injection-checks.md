---
id: TASK-5.8
title: 'Add agent guardrails: budgets, tool allowlists, tool-output injection checks'
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies:
  - TASK-5.1
references:
  - rag.py
parent_task_id: TASK-5
priority: high
type: feature
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An agent that can call tools needs limits a fixed pipeline did not. Bound every run and treat tool output as untrusted input.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each run has a maximum turn count and a token/dollar budget; hitting either ends the run with a clear message
- [ ] #2 Each interface declares which tools it may use (e.g. sandbox from Slack requires approval)
- [ ] #3 The prompt-injection check runs on tool results as well as user input
- [ ] #4 Tests cover budget exhaustion, a disallowed tool call and an injected tool result
<!-- AC:END -->
