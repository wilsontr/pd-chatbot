---
id: TASK-5.2
title: Widen PdPatch schema to every box type the renderer supports
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-24 22:01'
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
- [x] #1 The server-side schema and `frontend/src/types.ts` accept the same set of box types and fields
- [x] #2 Patches using each new box type validate on the server and render in the frontend
- [x] #3 Schema tests cover every box type
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Finding: types.ts only has the same four types as the server; the renderer's PD_UI_OBJECTS list merely keeps standalone 'obj' boxes with those names and draws them as rectangles. So the 11 names in that list become first-class types on both sides.
1. pd_schema.py and types.ts: add bng, tgl, hsl, vsl, hradio, vradio, vu, cnv, nbx, table, array; same five fields; text is the Pd creation text (name first) as for obj.
2. Viewer/layout: per-type drawing and sizes, wires use real box height, standalone types kept.
3. Keep dependants consistent: RETRY_PROMPT type list derived from the schema; patch_counts repair covers the new object types so typed widgets do not bypass TASK-5.24.
4. Tests: every type validates; parity test parses types.ts; count repair for a typed widget. Render every type in the frontend.
Out of scope: SYSTEM_PROMPT (changing it would move the eval baselines), 'obj'-typed widgets keep rendering as boxes, .pd serialisation (TASK-5.3).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
patch_counts.py needed no change: the corpus has no inlet/outlet counts for any widget or for table, and the typed 'array' is a data store that must not inherit [array define]'s counts. Validation: pytest 367 passed (332 before). Frontend build passes. Remaining tsc error (scroll-area.tsx) and lint error (App.jsx) predate this task. Rendered all 15 types through PdPatchViewer (SSR, headless Chrome screenshot) individually for new types and in one wired patch. The answer prompt still lists only the original four types, so the model will not emit the new ones until it is updated.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Server schema and frontend types now share 15 box types (added table, array, bng, tgl, hsl, vsl, hradio, vradio, nbx, vu, cnv). The viewer draws each new type and lays out variable-height boxes; the retry prompt's type list comes from the schema. Verified by 35 new tests, including types and fields parity against types.ts, and by rendering every type in headless Chrome.
<!-- SECTION:FINAL_SUMMARY:END -->
