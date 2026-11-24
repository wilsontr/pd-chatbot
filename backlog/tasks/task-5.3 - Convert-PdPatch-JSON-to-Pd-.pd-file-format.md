---
id: TASK-5.3
title: Convert PdPatch JSON to Pd .pd file format
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-24 22:25'
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
- [x] #1 Every supported box type and connection serializes to valid `.pd` syntax
- [x] #2 Generated files open in vanilla Pd without errors (checked by a test that loads them in headless Pd)
- [x] #3 Users can download any rendered patch from the web UI as a `.pd` file
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. pd_file.py: pure serializer PdPatch -> .pd text. Escapes text so it cannot inject records, writes full default argument lists for widgets, normalises table/array text, lays boxes out in ranks, drops wires to missing boxes and exact duplicates; other bad wires are kept so Pd reports them to the agent (TASK-5.4/5.5).
2. POST /patch/pd (API key, rate limit) returns the file; one serializer for web and agent instead of a second one in TypeScript. Object cap is higher than the model's 15 because the viewer renders oversized blocks.
3. PdPatchViewer: Download .pd button sending the rendered patch, with an error state.
4. Tests: format, escaping, endpoint, and a headless Pd load test covering every type, skipped without pd locally but required in CI (install Pd in eval.yml).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Headless tests run against vanilla Pd 0.57-0 (17 tests, including a control proving bad objects and wires are detected). Pd-l2ork fails only [array define]'s outlet, which a hand-written file also fails there. Download checked in a browser against the real endpoint, with the anchor click intercepted to read the file, plus the failure message. CI installs puredata-core; that step has not run yet. Limits: a floatatom's displayed value is not saved (Pd's format has no field for it), and blocks that fail the schema (e.g. an unknown type the viewer draws as a box) get the failure message.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added pd_file.py (PdPatch to .pd), POST /patch/pd and a Download .pd button. All 15 box types and their connections load without errors in headless vanilla Pd 0.57-0.
<!-- SECTION:FINAL_SUMMARY:END -->
