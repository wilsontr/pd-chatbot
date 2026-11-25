---
id: TASK-5.10
title: Add a run timeline replay page
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 20:30'
labels:
  - agent
dependencies:
  - TASK-5.9
references:
  - frontend/src
parent_task_id: TASK-5
priority: low
type: feature
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A frontend page that replays a stored run step by step. Useful for debugging and a strong portfolio demo.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Opening a run id shows its model turns, tool calls, results and artifacts in order
- [x] #2 Each step shows its tokens, cost and latency
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add run-log TypeScript types (RunSummary, RunDetail, StoredRunEvent) and pure helpers in frontend/src/lib/runTimeline.ts (converting stored events into ordered timeline steps with per-step tokens, cost, latency, and cumulative replay state) plus lightweight History API routing in frontend/src/lib/routes.ts.
2. Add API client helpers in frontend/src/lib/runsApi.ts for GET /api/runs and GET /api/runs/{run_id} with X-API-Key auth, and thread session_id/run_id/langfuse_trace_url through useChat, chatStream.ts, and MessageBubble (adding a Replay run link on completed messages).
3. Build the Run Timeline Replay UI (frontend/src/components/RunTimelinePage.tsx and RunListPage.tsx): run header with Langfuse trace link and totals, interactive step-by-step replay controls (Play/Pause, Prev/Next, scrubber, Replay from start), ordered timeline cards for model turns, tool calls, tool results, sources, text output/resets, live PdPatchViewer/PatchAudioPlayer artifacts, and done, each displaying tokens, cost, and latency.
4. Wire /runs and /runs/:runId routing in frontend/src/App.tsx and add SPA rewrites in frontend/vercel.json.
5. Add unit tests (frontend/src/lib/runTimeline.test.ts and chatStream.test.ts updates), run node --test, npm run build, npm run lint, and visually verify the replay page in the browser against a live local server.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented the Run Timeline Replay frontend page:
- Data model & timeline builder (frontend/src/types.ts, frontend/src/lib/runTimeline.ts, frontend/src/lib/routes.ts, frontend/src/lib/runsApi.ts): added RunSummary/RunDetail/StoredRunEvent types, pure buildTimelineSteps() converting stored run events into ordered timeline steps (model_turn, tool_call, tool_result, sources, collapsed text_output, text_reset, artifact, done) with per-step tokens/cost/latency and cumulativeMetrics(), History API routing (/runs and /runs/:runId), and API-key-authenticated GET /api/runs and GET /api/runs/{run_id} helpers.
- UI components (frontend/src/components/RunTimelinePage.tsx, RunListPage.tsx, MessageBubble.tsx, App.tsx, frontend/vercel.json): built the run browser (/runs with direct run-ID jump and session_id filter) and step-by-step replay page (/runs/:runId) with summary totals, Langfuse trace link, Replay/Play/Pause/Prev/Next/scrubber/Show-all controls, expandable per-step details (token breakdown, raw content blocks, tool arguments/output/chunks, live PdPatchViewer + PatchAudioPlayer artifacts), and a Replay run link on completed chat messages (threading session_id and run_id in useChat/chatStream.ts). Added SPA rewrites for /runs and /runs/:runId in frontend/vercel.json.
- Validation: node --test src/lib/*.test.ts (7 passed), npx tsc --noEmit (0 errors), npm run build (succeeded), npx eslint src (0 errors/warnings), and live browser verification at http://127.0.0.1:8765/runs and /runs/run-demo-bell confirming all 14 ordered steps, per-step tokens/cost/latency, live PdPatchViewer + PatchAudioPlayer rendering, interactive Replay/Show-all/expand controls, and zero console/network errors.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the /runs and /runs/:runId run timeline replay pages in frontend/src (RunTimelinePage, RunListPage, runTimeline.ts, runsApi.ts, routes.ts), linked completed chat messages to their run replay, and added Vercel SPA rewrites. Verified with node --test src/lib/*.test.ts (7 passed), tsc --noEmit, vite build, eslint src, and interactive browser verification of a 14-step run with PdPatchViewer and audio artifacts.
<!-- SECTION:FINAL_SUMMARY:END -->
