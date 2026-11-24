---
id: TASK-5.1
title: Refactor RAG pipeline into a tool-use agent core
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-24 05:48'
labels:
  - agent
dependencies: []
references:
  - rag.py
  - main.py
parent_task_id: TASK-5
priority: high
type: enhancement
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Move the request logic out of `rag.py`/`main.py` into a transport-independent agent package. Retrieval becomes a tool the model calls, rather than a fixed step. The loop emits a typed event stream (text deltas, tool calls, tool results, artifacts) so web, CLI, Slack and MCP can all be thin adapters.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Retrieval (hybrid search plus parent lookup) is exposed as a tool the model chooses to call
- [x] #2 The agent loop yields typed events and has no FastAPI or SSE imports
- [x] #3 The existing web chat works end to end on the new core, including streaming, caching, guardrails and patch validation
- [x] #4 The existing retrieval and generation evals run against the agent and the results are compared with the pipeline in the eval report
- [x] #5 Unit tests cover the loop with a stubbed model client
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extract the shared infrastructure out of rag.py into a new transport-free `agent/` package: typed events, model-client protocol, retrieval primitives, classification, prompts, guardrails, patch validation, response cache, observability, history compression. rag.py keeps the fixed pipeline (classify -> retrieve -> generate -> validate) as the eval baseline and imports the same primitives.
2. Expose retrieval as a model-facing tool (`search_docs`) with a JSON schema, returning formatted parent excerpts plus the chunks for sources/faithfulness; keep the hybrid RRF + object-name + relevance-cutoff behaviour.
3. Add the hand-written tool-use loop in `agent/loop.py`: async generator over an injected ModelClient, streaming text deltas and yielding typed events (text_delta, tool_call, tool_result, artifact, sources, done); bounded turns.
4. Add request orchestration on top of the core in `agent/chat.py` (injection check, cache, scope gate, pd-patch canonicalisation/artifacts, faithfulness guard, cost + tracing) with no FastAPI/SSE imports.
5. Rebuild main.py as a thin SSE adapter over the typed stream, preserving the existing meta/chunk/done contract and forwarding tool/artifact events.
6. Extend the eval harness: generation `--mode pipeline|agent|both`, an agent-tool retrieval strategy, agent metrics (tool calls, retrieval calls) and a pipeline-vs-agent report section; the CI gate stays on the pipeline arm.
7. Unit-test the loop with a stubbed model client (single turn, tool call + answer, turn budget, tool failure, no-retrieval answer), the tool contract, and the SSE adapter; keep the existing suite green.
8. Run the retrieval and generation evals for both arms, write the measured comparison into the eval report, and update README/docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Core refactor landed. New `agent/` package: events (typed stream), llm (ModelClient boundary + Anthropic streaming client), retrieval (shared hybrid search + parent lookup + relevance backstop), classification, prompt (shipped prompt + tool rules), guardrails, patches, cache, observability, history, tools/ (ToolSpec/ToolOutcome/registry + search_docs), loop (hand-written tool-use loop) and chat (request orchestration). rag.py is now the fixed-pipeline baseline over the same primitives, and main.py is a thin SSE adapter over the typed stream.

Loop guarantees: sources before the first text delta; text emitted alongside a tool_use block is treated as narration and dropped from the answer; max_turns with a forced final answer (tools disabled) rather than an empty result; unknown/failing tools return model-visible recovery text instead of raising; token usage summed across turns.

Verification evidence.

1) Retrieval as a tool: live runs show the model choosing the call — `search_docs {query: "osc~ inlets outlets", object_names: ["osc~"], content_type: "object_reference"}` for an inlets question, and two model-chosen calls for an FM-bell question. tests/test_search_tool.py (10 tests) covers the schema, argument handling and the no-match message.

2) Typed, transport-free loop: agent/events.py defines TextDelta/ToolCall/ToolResult/Sources/Artifact/Done; `grep -rn 'fastapi|starlette|StreamingResponse|text/event-stream' agent/` returns nothing, and tests/test_agent_chat.py::TestInterfaceIndependence asserts it. tests/test_agent_loop.py (17 tests, scripted StubClient) asserts event order, sources-before-text, the tool-result round trip, unknown/failed tools, and the turn budget.

3) Web chat end to end: a live POST /chat through the FastAPI app (real model + Chroma) returned frames tool_call, tool_result, tool_call, tool_result, meta, chunk x N, artifact, done (with a stored message_id); the artifact was a validated 9-object pd-patch. Tests cover the cache-hit replay, off-topic/policy refusal with no model call, injection block, mixed-scope note, artifact emission, invalid-patch placeholder + flag, and the low-faithfulness disclaimer. Full suite: 221 passed, 4 deselected.

4) Evals against the agent, compared in the report: `python -m evaluation.evaluate --all --mode both --agent-retrieval --sample 10 --report docs/EVAL.md`. docs/EVAL.md now carries the "Agent tool calls" retrieval row and a "### Pipeline vs agent" section. Measured (run 2, after the prompt fix): retrieval agent tool calls recall@5 76.11% vs hybrid 75.00%, MRR 0.753 vs 0.696; generation faithfulness 4.70 vs 4.70, relevance 5.00 vs 4.90, citation 4.90 vs 5.00, cost/query $0.0087 vs $0.0097, p50 5.0 s vs 3.3 s, 1.2 tool calls/entry. Measured generation cost for the both-arm run: $0.184.

5) Loop unit tests with a stubbed model client: tests/test_agent_loop.py — 17 tests.

First eval run (before the prompt fix) exposed a real failure the comparison exists to catch: the agent stretched a general "Subpatches" section to support a claim about [unpack], scoring citation 4.40 vs 5.00. Added two tool-guidance rules (prefer object_reference when the question names objects; attribute a claim only to the section that covers it; flag anything beyond the excerpts); the second run is at parity. I did not tune further: with 10 entries and judge noise of ~0.1–0.3, the honest conclusion is parity on answers, better retrieval ranking, slightly cheaper and ~1.7 s slower per answer.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Moved the request path out of rag.py/main.py into a transport-independent `agent/` package and put the fixed pipeline in rag.py as the evaluation baseline. Retrieval is now a model-facing `search_docs` tool over the same hybrid RRF + parent-lookup code; the hand-written loop streams text, runs tool calls off the event loop, guarantees citations before the first token, discards tool-call narration, and falls back to a forced final answer at the turn budget. main.py is a thin SSE adapter over the typed event stream, keeping the meta/chunk/done contract and forwarding tool/artifact frames. The eval harness gained `--mode pipeline|agent|both`, `--agent-retrieval` and a Pipeline-vs-agent report section; the CI gate stays on the calibrated pipeline arm, while PRs now run both arms.

Verified: 221 tests pass (17 loop tests against a scripted model client, 10 tool-contract tests, 12 eval-support tests); a live POST /chat returned the full frame sequence with a validated pd-patch artifact and a stored message id; `--all --mode both --agent-retrieval --sample 10` regenerated docs/EVAL.md. Measured: agent tool calls beat the pipeline's single retrieval on MRR (0.753 vs 0.696) and recall@5 (76.1% vs 75.0%); answer quality is at parity within judge noise (faithfulness 4.70 vs 4.70, relevance 5.00 vs 4.90, citation 4.90 vs 5.00) at slightly lower cost ($0.0087 vs $0.0097 per query) and +1.7 s p50. The first run, before a prompt fix, caught a real misattribution (4.40 vs 5.00 citation); that finding and the fix are recorded in the notes.
<!-- SECTION:FINAL_SUMMARY:END -->
