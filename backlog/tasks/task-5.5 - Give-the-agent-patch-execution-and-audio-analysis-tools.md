---
id: TASK-5.5
title: Give the agent patch execution and audio analysis tools
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 00:46'
labels:
  - agent
dependencies:
  - TASK-5.1
  - TASK-5.3
  - TASK-5.4
parent_task_id: TASK-5
priority: high
type: feature
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add `write_patch`, `run_patch` and `analyze_audio` tools so the agent can build a patch, run it in the sandbox, inspect the result (RMS, peak frequency, spectrum summary, clipping/silence) and fix it. Making patches a tool call also replaces the current fenced `pd-patch` block, which is coaxed out by prompt text and parsed afterwards, with structured tool input.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Patches are produced through a tool call with a JSON schema; fenced `pd-patch` parsing is no longer needed
- [x] #2 The agent can run a patch, read errors and audio analysis, and revise the patch within one request
- [x] #3 An eval metric reports the share of answers whose patches run cleanly and meet the question's spec, with and without execution tools
- [x] #4 Rendered audio is returned as an artifact event the interfaces can play
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Per-request PatchWorkspace (latest version of each named patch, its last run) shared by three tools: write_patch (PdPatch JSON schema), run_patch (sandbox, optional simulated inputs so interactive patches can be tested), analyze_audio (numpy).
2. write_patch is always offered; run_patch/analyze_audio only when execution is enabled (PD_SANDBOX_BACKEND set), with matching prompt rules. The pipeline baseline (rag.py) keeps fenced pd-patch blocks and its prompt byte for byte, so the pipeline-vs-agent comparison stays valid.
3. Loop: text streamed in a turn that then calls a tool is retracted with a text_reset event.
4. Patches become pd_patch artifacts; the final audio of an unchanged patch becomes an audio artifact (id + URL, bytes in a size-capped TTL store served by GET /artifacts/{id}). Count repair moves from fenced text to patch objects. Cache and feedback store keep the patches.
5. Frontend renders patch artifacts under the answer instead of parsing fences. Playing audio and step display stay in TASK-5.6.
6. Faithfulness guard and eval judge get the patch and run results as evidence.
7. evaluation/patch_eval.py + patch_tasks.json: share of answers whose patch runs cleanly and meets a machine-checked spec, with and without execution tools. Run it, plus the existing generation eval sample for regressions.
8. Tests, docs (AGENTIC, EVAL, README). Live run from Railway needs a deploy.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC evidence. #1: write_patch is the agent's only patch path; agent/chat.py and the frontend no longer parse fences (rag.py, the eval's pipeline baseline, still does; its prompt is byte-identical to before). #2: live API run on Modal wrote, ran, analysed and revised a patch within one request (4 revisions). #3: evaluation/patch_eval.py, 16 tasks x 3 trials: 73% without, 92% with execution (docs/PATCH_EVAL.md). #4: audio artifact event with id/URL, bytes fetched from GET /artifacts/{id}.
Found by measuring: the first version dropped answer text written beside write_patch (relevance 5.00 -> 4.58); fixed in the loop (4.93).
Validation: 648 passed, 17 skipped (Pd not installed); sandbox and patch-tool tests also pass on Modal (124 passed, 1 skipped); web UI checked in a browser.
Left open: one live run from Railway after deploy, so the task stays In Progress. Audio playback and step display in the UI are TASK-5.6; tool-output injection checks and per-run budgets are TASK-5.8. Patch tests that need the sandbox image are not in a CI workflow yet. Text can appear and be retracted (text_reset) while the agent iterates; hard tasks take 20-40 s.
<!-- SECTION:NOTES:END -->
