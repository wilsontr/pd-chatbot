---
id: TASK-5.2
title: Widen PdPatch schema to every box type the renderer supports
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies: []
references:
  - pd_schema.py
  - frontend/src/types.ts
parent_task_id: TASK-5
priority: medium
type: enhancement
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`PdObject.type` in `pd_schema.py` only allows `obj`, `msg`, `floatatom` and `comment`, but `frontend/src/types.ts` and the SVG renderer also support tables/arrays and UI widgets (`bng`, `tgl`, sliders, etc.). Patches must be expressible in full before they can be turned into runnable `.pd` files.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The server-side schema and `frontend/src/types.ts` accept the same set of box types and fields
- [ ] #2 Patches using each new box type validate on the server and render in the frontend
- [ ] #3 Schema tests cover every box type
<!-- AC:END -->
