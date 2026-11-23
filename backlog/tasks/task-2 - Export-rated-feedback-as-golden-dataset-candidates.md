---
id: TASK-2
title: Export rated feedback as golden-dataset candidates
status: To Do
assignee: []
created_date: '2026-11-23 20:39'
labels:
  - feedback
  - evaluation
dependencies:
  - TASK-1
references:
  - main.py
  - evaluation/dataset.py
priority: medium
type: feature
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Close the loop between production usage and offline evaluation. The frontend already collects thumbs up/down and the backend persists them, but nothing turns that data into test cases. Downvoted answers are the most valuable source of new hard eval questions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A CLI command exports feedback rows (filterable by rating and date range) in the same JSON shape as `evaluation/golden_dataset*.json`, with source sections pre-filled from the stored chunks
- [ ] #2 Exported candidates are written to a separate review file and never added to the golden set automatically
- [ ] #3 The README evaluation section explains the feedback-to-eval workflow
<!-- AC:END -->
