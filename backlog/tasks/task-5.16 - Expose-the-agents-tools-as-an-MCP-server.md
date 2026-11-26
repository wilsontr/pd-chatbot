---
id: TASK-5.16
title: Expose the agent's tools as an MCP server
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:41'
updated_date: '2026-11-26 07:13'
labels:
  - agent
dependencies:
  - TASK-5.1
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Publish docs search and (once available) patch execution as an MCP server, so Claude Code, Claude Desktop and other MCP clients can use them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Docs search and patch tools are callable from an MCP client
- [x] #2 The same guardrails apply as in other interfaces
- [x] #3 The README shows how to add the server to an MCP client
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extract the per-call guard (interface ToolPolicy check, unknown-tool handling, tool exception capture, tool-output injection check) from agent/loop.py and agent/durable.py into one shared run_guarded_tool() in agent/guardrails.py, so every interface goes through the same code.
2. Add agent/mcp_server.py: an MCP server (official mcp SDK, low-level Server, stdio transport) that builds the tool set with default_registry(interface='mcp') — search_docs, list/read/write_patch, load_skill, and run_patch/analyze_audio when PD_SANDBOX_BACKEND is set — advertises each tool's existing JSON schema unchanged, and runs calls through run_guarded_tool with policy_for('mcp'). One PatchWorkspace per server process, so the per-session patch/run limits apply. write_patch results also return the .pd file text since MCP clients have no diagram renderer.
3. Add mcp to requirements.txt; entry point python -m agent.mcp_server.
4. Tests: in-memory MCP client session lists tools and calls search_docs (stubbed retrieval) and write_patch/read_patch; policy block, injection block, and sandbox tools hidden without a backend.
5. README: MCP section with Claude Code (claude mcp add) and Claude Desktop config examples.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Shared guard: run_guarded_tool() in agent/guardrails.py now holds the policy check, unknown-tool handling, exception capture and tool-output injection screen. agent/loop.py and agent/durable.py both call it (removing two diverging copies; durable's blocked-output message now matches the loop's). Loop counters are derived from outcome.data (disallowed_tool / injection_blocked).
- MCP server: agent/mcp_server.py uses the official mcp SDK (2.3.0) low-level Server over stdio, so the tools' existing JSON schemas are advertised unchanged and no model runs server-side. Tools listed = default_registry(interface='mcp') filtered by policy.permitted_names, so run_patch/analyze_audio appear only with PD_SANDBOX_BACKEND and approval-gated tools stay hidden until approved. Calls are serialized with a lock (in-memory PatchWorkspace). Results carry is_error, a structured {ok, summary, data, sources}, and write_patch also returns the .pd file text since MCP clients cannot render diagrams.
- Launcher: root mcp_server.py chdirs to the repo root (chroma_db, chunk files, .env are cwd-relative) so clients can start it from anywhere.
- Validation: tests/test_mcp_server.py (16 tests) drives the server through an in-process mcp Client: listing, schema parity, search_docs with sources, write/list/read patch with .pd output, run/analyze via a fake sandbox, policy refusal, slack-style approval gating, injection blocking, MAX_RUNS limit, tool crash, plus a stdio end-to-end test launching mcp_server.py from a temp dir. Full suite: 759 passed, 81 skipped (baseline 743).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Published the agent's tools as a stdio MCP server (agent/mcp_server.py, launcher mcp_server.py): search_docs, list/read/write_patch (write_patch returns the .pd file), load_skill, and run_patch/analyze_audio when a sandbox backend is set. Extracted run_guarded_tool() so the agent loop, the durable loop and MCP share one guard path (interface ToolPolicy, exception capture, tool-output injection screen); the patch tools' per-session limits apply per server process. README has Claude Code and Claude Desktop setup. Verified with 16 new tests through a real MCP client (including a stdio end-to-end launch) and the full suite (759 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
