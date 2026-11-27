---
id: TASK-5.30
title: 'Decide tool approval on the server, not from the request'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-27 21:45'
labels:
  - agent
  - security
dependencies: []
references:
  - main.py
  - agent/durable.py
  - agent/guardrails.py
parent_task_id: TASK-5
priority: high
type: bug
ordinal: 40000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`POST /runs/durable` takes `interface` and `approved_tools` from the request body (DurableRunRequest in main.py), so a caller can pick an interface without approval rules or pre-approve `run_patch` and skip the human gate that ToolPolicy.require_approval is meant to enforce (agent/guardrails.py, agent/durable.py). `POST /runs/{run_id}/approve` can be called by anyone holding the public API key, so the approver is not authenticated either. The Slack interface (TASK-5.18) relies on this gate. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The interface and its tool policy are set by the server-side caller, not by request fields
- [x] #2 A request cannot pre-approve a tool that its interface requires approval for
- [x] #3 Only an authorised approver can approve or deny a paused run, and the decision is recorded with who made it
- [x] #4 Tests cover a forged interface, forged approved_tools and an unauthorised approval
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. HTTP durable runs: DurableRunRequest forbids extra fields and no longer takes interface, approved_tools or pause_for_approval; the endpoint always starts a web-interface run. Other interfaces (Slack, CLI, eval) start runs in-process through start_durable_run, which stays the server-side place to choose an interface and pre-approve tools.
2. Approvers: ADMIN_API_KEYS entries become name:key (a bare key is named 'operator'); POST /runs/{id}/approve requires X-Admin-Key and 403s otherwise, including for the run's own session.
3. Record who decided: approve_run takes a required approver; the DBOS message carries it; ApprovalResolved gains an approver field that the store replays and the CLI prints.
4. Tests: forged interface / approved_tools / pause_for_approval are 422; a server-side Slack run pauses and cannot be approved without an operator key (none, wrong, or the session id); an operator approval completes the run and records the approver; approve_run refuses an empty approver.
5. Docs: README API auth and AGENTIC durable notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design: the HTTP durable endpoint is just one adapter, so it gets one fixed interface (HTTP_DURABLE_INTERFACE = web) and DurableRunRequest forbids extra fields (interface, approved_tools, pause_for_approval are now 422, not silently ignored). Adapters that need approval rules (Slack, TASK-5.18) start runs in-process with start_durable_run and their own interface. Approvers are operators: ADMIN_API_KEYS entries are name:key (bare key = 'operator', so TASK-5.29's format still works); POST /runs/{id}/approve 403s without a matching X-Admin-Key, including for the run's own session, and passes the name to approve_run, which now requires an approver. The name travels in the DBOS message and is stored on ApprovalResolved.approver (replayed by the store, printed by the CLI as 'by <name>'; empty on timeout).

Compatibility: _step_resolve_approval gained an approver argument; a durable run paused before this deploy resumes with an empty approver because the workflow reads it from the message with a default.

Validation:
- pytest tests: 813 passed, 87 skipped (sandbox image not available). tests/test_durable_runs.py: forged interface (slack, eval), approved_tools and pause_for_approval each 422 with no run stored; an HTTP run records interface web and only builds web tools; a server-side Slack run pauses, and approval with only the browser key, with the requester's session id, or with a wrong operator key is 403 while the tool stays unrun; an operator approval completes the run and stores approver 'alice'; approve_run refuses a blank approver; the approver round-trips through the run store; timeouts store an empty approver.
- Live local server: POST /runs/durable with interface or approved_tools -> 422; POST /runs/{id}/approve with only the API key -> 403 'Approving a run needs an operator key'.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Tool approval is now decided on the server. POST /runs/durable always runs under the web interface and refuses interface, approved_tools and pause_for_approval fields (422); adapters that need approval, like Slack, start runs in-process with their own interface. Only an operator key (ADMIN_API_KEYS, name:key, sent as X-Admin-Key) can approve or deny a paused run; the run's own session cannot. approve_run requires an approver, and the name is stored on the approval_resolved event and shown by the CLI. Verified with new durable HTTP tests (forged fields, unauthorised approvals, recorded approver), the full suite (813 passed, 87 sandbox skips), and a check against a live local server.
<!-- SECTION:FINAL_SUMMARY:END -->
