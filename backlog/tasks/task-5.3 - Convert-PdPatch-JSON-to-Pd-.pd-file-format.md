---
id: TASK-5.3
title: Convert PdPatch JSON to Pd .pd file format
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies:
  - TASK-5.2
references:
  - pd_schema.py
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a serializer from the validated patch model to Pd's native `.pd` text format, so the agent's patches can be executed, downloaded and opened in the Pd editor.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every supported box type and connection serializes to valid `.pd` syntax
- [ ] #2 Generated files open in vanilla Pd without errors (checked by a test that loads them in headless Pd)
- [ ] #3 Users can download any rendered patch from the web UI as a `.pd` file
<!-- AC:END -->
