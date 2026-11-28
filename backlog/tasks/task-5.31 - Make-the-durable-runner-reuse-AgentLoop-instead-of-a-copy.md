---
id: TASK-5.31
title: Make the durable runner reuse AgentLoop instead of a copy
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-27 23:10'
labels:
  - agent
dependencies: []
references:
  - agent/durable.py
  - agent/loop.py
  - tests/test_durable_runs.py
parent_task_id: TASK-5
priority: medium
type: enhancement
ordinal: 41000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
agent/durable.py reimplements the agent loop in `durable_agent_workflow` rather than wrapping agent/loop.py, and the two have drifted. The durable path keeps only the last turn's text, so text written beside `write_patch` is lost; it has no faithfulness check; and it does not track tool errors, recoveries, disallowed calls or blocked injections. Any loop change (for example TASK-5.27's budgets) has to be made twice. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 One implementation of the turn logic (text handling, budgets, error and recovery accounting) serves both the streaming and the durable paths
- [x] #2 A durable run and a streaming run given the same scripted model turns produce the same answer text and the same run metrics
- [x] #3 Durable runs still checkpoint each model turn and tool call and still resume after a restart, shown by the existing durable-run tests
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Give AgentLoop two seams: _turn_pieces (how a model turn is produced, with its tracing) and _execute_tool (how one call is run); split turn tracing from token/cost accounting.
2. In agent/durable.py replace the hand-written workflow loop with a DurableAgentLoop subclass: its turn seam runs one checkpointed DBOS step and replays the turn's text as a delta; its tool seam runs the approval gate and a checkpointed tool step that carries the workspace snapshot.
3. The workflow iterates AgentLoop.stream and persists every event through one checkpointed record step, so seqs stay deterministic on replay; approval events land between ToolCall and ToolResult.
4. Finish step adds the faithfulness check (runtime.faithfulness_fn, check_faithfulness in production) and returns the loop's error/recovery/guardrail counters.
5. Tests: same scripted turns through AgentLoop and the durable workflow give the same answer and metrics (text beside write_patch, a failed then clean run_patch, a budget stop); existing crash/approval tests still pass.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AgentLoop now has two seams, _turn_pieces (produce one model turn, with tracing) and _execute_tool (run one call); turn tracing moved to loop.trace_turn and token/cost accounting to _account_turn. agent/durable.py's hand-written loop is gone: DurableAgentLoop subclasses AgentLoop, runs each turn in _step_model_turn (its text replayed as one delta) and each call in an async _step_execute_tool carrying the workspace snapshot, and the workflow persists every yielded event through a checkpointed _step_record_event with one seq counter. Approval events now sit between the ToolCall and its ToolResult (before: ahead of the ToolCall). A denied or timed-out approval is a failed tool result, so it now counts as a tool error. The durable finish step runs the faithfulness guard (DurableRuntime.faithfulness_fn, check_faithfulness in production) and the workflow result carries retrieval_calls, tool_errors, recovered_errors, unresolved_errors, disallowed_tool_calls and blocked_tool_outputs.

Deploy note: the step sequence changed, so launch_dbos's application_version is now 2.0.0. A durable run left paused or mid-run by the old code is not recovered by the new version (DBOS only recovers its own version); it stays pending. Known: New Relic counters raised inside the loop (budget exceeded, skill loaded) fire again when a workflow is replayed after a restart.

Validation: tests/test_durable_runs.py TestSameLoopAsStreaming runs the same scripted turns through AgentLoop and the durable workflow and compares answer and metrics for a repair (failed then clean run_patch), text beside write_patch, a token-budget stop and a turn limit; plus a faithfulness-disclaimer test. Existing crash-restart (real two-process os._exit) and approval tests pass unchanged. Full suite: 820 passed, 87 skipped (sandbox image not available locally).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The durable runner now reuses AgentLoop: DurableAgentLoop overrides only how a model turn and a tool call run (as DBOS steps, with the approval gate), so text handling, budgets and error/recovery accounting are one implementation. Durable runs now keep text written beside write_patch, run the faithfulness guard and report the loop's error, recovery and guardrail counters. DBOS application_version bumped to 2.0.0 because the step sequence changed. Verified by new durable-vs-streaming equivalence tests over four scripts, the unchanged crash-restart and approval tests, and the full suite (820 passed, 87 skipped).
<!-- SECTION:FINAL_SUMMARY:END -->
