---
id: TASK-5.8
title: 'Add agent guardrails: budgets, tool allowlists, tool-output injection checks'
status: Done
assignee:
  - '@antigravity'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 06:24'
labels:
  - agent
dependencies:
  - TASK-5.1
references:
  - rag.py
parent_task_id: TASK-5
priority: high
type: feature
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An agent that can call tools needs limits a fixed pipeline did not. Bound every run and treat tool output as untrusted input.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each run has a maximum turn count and a token/dollar budget; hitting either ends the run with a clear message
- [x] #2 Each interface declares which tools it may use (e.g. sandbox from Slack requires approval)
- [x] #3 The prompt-injection check runs on tool results as well as user input
- [x] #4 Tests cover budget exhaustion, a disallowed tool call and an injected tool result
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extend agent/guardrails.py with ToolPolicy, per-interface allowlists (INTERFACE_POLICIES for web, cli, slack, mcp, eval — with Slack requiring explicit approval for sandbox tools run_patch/analyze_audio), policy_for(), and check_tool_output_injection(); fix case-sensitive whole-word \bDAN\b pattern so legitimate Pd manual excerpts (e.g. Dan Wilcox) produce 0 false positives.
2. Add ToolRegistry.filtered() in agent/tools/__init__.py; add max_tokens_budget, max_cost_usd, tool_policy, and approved_tools to AgentConfig in agent/loop.py, tracking total_tokens, cost_usd, budget_exceeded, blocked_tool_outputs, and disallowed_tool_calls on LoopState.
3. Enforce run budgets (max_turns, max_tokens_budget, max_cost_usd) with clear user-facing termination messages, enforce tool allowlists/approvals at both schema and execution time, and run check_tool_output_injection on every tool result (replacing tainted content and stripping tainted chunks before the next model turn).
4. Thread interface/policy/approved_tools through agent/chat.py (default_registry, default_config, stream_chat, answer_question), expose budget_exceeded on AgentAnswer/Done, and declare interface='web' in main.py.
5. Add unit tests covering turn/token/dollar budget exhaustion, disallowed & approval-gated tool calls, and injected tool results (including zero false positives on Pd docs); run pytest and finalize TASK-5.8.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented agent guardrails:
- Run budgets (agent/loop.py, agent/chat.py, agent/events.py): added DEFAULT_MAX_TOKENS_BUDGET (40,000) and DEFAULT_MAX_COST_USD ($0.10) alongside max_turns on AgentConfig, tracked total_tokens/cost_usd/budget_exceeded on LoopState/AgentAnswer/Done, stopped runs immediately on token/cost exhaustion (and fell back to BUDGET_MESSAGES["turns"] if an uncooperative final turn still produced no text), recorded Custom/Agent/BudgetExceeded/{turns,tokens,cost} metrics, and skipped caching budget-exhausted incomplete answers.
- Per-interface tool allowlists (agent/guardrails.py, agent/tools/__init__.py, agent/loop.py, agent/chat.py, main.py): added ToolPolicy, INTERFACE_POLICIES (web, cli, slack, mcp, eval — with Slack requiring explicit approval for sandbox tools run_patch/analyze_audio), policy_for(), and ToolRegistry.filtered(); enforced policies at both schema advertisement and execution time (recording Custom/Agent/ToolDisallowed without invoking tool.run), and declared interface="web" in main.py.
- Tool-output prompt-injection checks (agent/guardrails.py, agent/loop.py): added check_tool_output_injection() covering both tool content and retrieved chunks, running on every tool result in AgentLoop._run_tool (blocking tainted content/chunks with Custom/Guard/ToolOutputInjectionBlocked before the next model turn). Fixed case-sensitive word-boundary \bDAN\b pattern so all 566 legitimate Pd documentation chunks in parent_chunks.json produce 0 false positives (previously "Dan" matched 2 Pd manual chapters).
- Validation: pytest -m "not slow" passed (610 passed, 81 skipped), including 12 new tests in tests/test_agent_guardrails.py covering turn/token/cost budget exhaustion, disallowed & approval-gated tool calls, injected tool output & chunks, and zero false positives across parent_chunks.json.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added per-run turn/token/dollar budgets with clear termination messages, per-interface tool allowlists and approval policies (including Slack sandbox approval gating), and prompt-injection checks on all tool outputs and chunks (fixing a \bDAN\b false positive on Pd manual text). Verified with pytest -m "not slow" (610 passed, 81 skipped), including tests/test_agent_guardrails.py covering budget exhaustion, disallowed/approval-gated tool calls, and injected tool outputs.
<!-- SECTION:FINAL_SUMMARY:END -->
