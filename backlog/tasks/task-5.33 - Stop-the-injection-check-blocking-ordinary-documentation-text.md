---
id: TASK-5.33
title: Stop the injection check blocking ordinary documentation text
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - security
dependencies: []
references:
  - agent/guardrails.py
  - tests/test_agent_guardrails.py
parent_task_id: TASK-5
priority: low
type: bug
ordinal: 43000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The prompt-injection patterns in agent/guardrails.py flag ordinary text: "If you are not certain, check the help file" matches because the "you are ... (assistant|...|ai)" pattern has no word boundary and "certain" contains "ai", and "the system message box" matches the system-prompt pattern. No current doc chunk trips them (0 of 2,503), but new documentation or a patch's `[print]` output could, and a match blocks the whole tool result. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ordinary Pd documentation phrasing (including the two examples above) is not flagged
- [ ] #2 The existing injection test cases are still flagged
- [ ] #3 A test runs the check over the whole indexed corpus and expects no matches
<!-- AC:END -->
