---
id: TASK-5.6
title: Show agent steps and rendered audio in the web UI
status: Done
assignee:
  - '@antigravity'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 05:49'
labels:
  - agent
dependencies:
  - TASK-5.5
references:
  - frontend/src
parent_task_id: TASK-5
priority: medium
type: enhancement
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Make the agent visible in the existing frontend: tool calls appear as collapsible steps while the answer streams, and rendered audio from sandbox runs can be played next to the patch diagram.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Tool calls and results stream in as collapsible steps
- [x] #2 Audio artifacts play inline beside their patch diagram
- [x] #3 Answers without tool use look the same as today
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extend frontend/src/types.ts with AgentStep and AudioArtifact and update frontend/src/hooks/useChat.ts (with a stream-event helper) to handle tool_call, tool_result, and audio artifact events, creating the assistant message on the first tool_call or meta event so steps stream in before meta arrives.
2. Build frontend/src/components/AgentSteps.tsx: collapsible outer disclosure (expanded while streaming, auto-collapsed on completion unless user-toggled) with per-step collapsible details for search_docs, write_patch, run_patch (highlighting non-clean Pd runs/errors), analyze_audio, and generic tools.
3. Build frontend/src/components/PatchAudioPlayer.tsx fetching /api/artifacts/{id} with X-API-Key into a managed blob URL with native inline <audio controls>, loading, expired (404), and retry states; pair audio artifacts with their matching patch diagram by name side-by-side in MessageBubble.tsx (flex-wrap beside PdPatchViewer).
4. Keep answers without tool use visually identical to today (render AgentSteps only when steps exist, render audio player only when audio artifacts exist).
5. Verify with tsc, vite build, eslint, pytest, and an interactive browser check exercising streaming tool steps, inline audio playback beside a patch diagram, and a plain non-tool answer.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Added AgentStep/AudioArtifact types and pure SSE stream reducer (frontend/src/lib/chatStream.ts), updated useChat to create the assistant message on the first tool_call or meta event and capture tool_call, tool_result, and audio artifacts. Built AgentSteps (auto-expanded while streaming, auto-collapsed on done unless toggled, per-step expandable inputs/results/errors) and PatchAudioPlayer (authenticated fetch via /api/artifacts/{id} with X-API-Key into a managed blob URL with loading/expired/retry states) laid out side-by-side with PdPatchViewer in MessageBubble.
Validation: node --test --experimental-strip-types frontend/src/lib/chatStream.test.ts (3/3 passed); npm run build succeeded (0 TS errors in edited/new files; pre-existing unused React import in scroll-area.tsx and unused err in legacy frontend/App.jsx untouched); pytest -m 'not slow' (584 passed, 81 skipped, including test_pd_schema.py); interactive browser check against compiled dist + local mock SSE/WAV server confirmed live streaming steps, auto-collapse/expand with Pd errors and audio stats, authenticated WAV playback inline beside the patch SVG, and unchanged rendering for non-tool answers with 0 console/network errors.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented collapsible streaming agent steps (AgentSteps) and inline authenticated audio playback beside patch diagrams (PatchAudioPlayer) in the web UI while preserving existing rendering for non-tool answers. Verified with unit tests (frontend/src/lib/chatStream.test.ts: 3/3 passed), Vite production build, pytest -m 'not slow' (584 passed), and an end-to-end browser verification covering streaming tool calls/results, inline WAV playback beside the patch diagram, and a plain non-tool answer.
<!-- SECTION:FINAL_SUMMARY:END -->
