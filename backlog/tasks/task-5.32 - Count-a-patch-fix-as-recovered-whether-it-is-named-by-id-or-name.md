---
id: TASK-5.32
title: Count a patch fix as recovered whether it is named by id or name
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-27 23:12'
labels:
  - agent
  - eval
dependencies: []
references:
  - agent/loop.py
  - agent/workspace.py
parent_task_id: TASK-5
priority: low
type: bug
ordinal: 42000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`_tool_error_key` in agent/loop.py identifies a patch only by the `name` argument. When the model refers to a stored patch by `id` (TASK-5.12), a failed `run_patch` and the later clean run get different keys, so the recovery is not counted and the error stays unresolved. This skews the recovery metric that the agent gate checks. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A failed run and a later clean run of the same patch count as one recovery whether each call used the id or the name
- [x] #2 Tests cover the id/name mixes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Key a patch tool's error episode by the stored patch name the result reports, falling back to the call's name, then id.
2. Make write_patch, run_patch and analyze_audio failures report the stored name when the call's id or name resolves to a stored patch.
3. Test id/name mixes through AgentLoop with the real run_patch tool.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
agent/loop.py _tool_error_key now takes the outcome and keys write_patch/run_patch/analyze_audio by outcome.data['name'] (the stored name), else the name argument, else the id argument. Successful patch-tool results already carried the stored name; failures did not, so _PatchTool._named adds it to a failure whose id or name resolves to a stored patch (e.g. a rejected seconds value, a sandbox outage). Before, keying was by the name argument only, so an id call and a name call never matched.

Validation: tests/test_patch_tools.py TestRecoveryByIdOrName: unclean-then-clean run for name/name, id/id, id/name, name/id; rejected-then-clean for both mixes; a different patch does not resolve it; a failure reports the stored name. With the old key the four mixed cases fail. Existing recovery tests in tests/test_agent_metrics.py pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A patch fix now counts as one recovery whether each call named the patch by id or by name: the loop keys patch errors by the stored name the tool result reports, and patch-tool failures now report it. Verified by new id/name mix tests through AgentLoop (four of which fail with the old key) and the existing recovery-metric tests.
<!-- SECTION:FINAL_SUMMARY:END -->
