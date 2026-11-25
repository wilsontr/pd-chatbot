---
id: TASK-5.12
title: Keep patches in a per-session workspace for iterative edits
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 21:59'
labels:
  - agent
dependencies:
  - TASK-5.5
  - TASK-5.9
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Patches the agent creates should persist for the session, so follow-ups like "make the filter resonant" edit the existing patch rather than starting over.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Patches created in a session are stored and listed by id
- [x] #2 The agent can load and modify a stored patch in a follow-up turn
- [x] #3 Users can download the latest version of each session patch as `.pd`
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Persist session patches in the app database: a session_patches table (session_id, stable patch_id, name, JSON patch, revision, timestamps) with a SessionPatchStore that seeds/saves a PatchWorkspace, shares the RunStore engine, and prunes old rows.
2. Extend PatchWorkspace/PatchEntry with patch_id, pd_url, revision and a touched flag, plus seeding and artifact data, and keep them in the durable snapshot.
3. Add list_patches and read_patch tools (resolve by id or name), let write_patch/patch tools accept an id and continue revisions, and raise the per-workspace patch cap for a session. Add the new tools to interface policies.
4. Add a prompt rule to load an existing session patch before editing it instead of starting over.
5. Orchestration: seed the workspace from the session store, repair only patches written this turn, persist after repair, and emit artifacts carrying id/revision/pd_url. Adopt cached patches into the current session without clobbering newer revisions.
6. Durable path: load the session workspace in a checkpointed step and save it in the finish step.
7. API: GET /sessions/{session_id}/patches and GET /sessions/{session_id}/patches/{patch_id}.pd; wire the store into main and DurableRuntime.
8. Frontend: show revisions, download the latest .pd by patch id, and list every session patch with a download button.
9. Tests for the store, workspace, tools, orchestration, durable path and API; run the full suite.
10. Docs (AGENTIC, README) and task notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Session patch workspace implemented (backend + UI):
- agent/sessions.py: session_patches table (session_id, stable patch_id, name, JSON patch, revision, timestamps) with SessionPatchStore (upsert, get by id or name, list, seed/save a PatchWorkspace, prune after 30 days). Shares the RunStore database and engine (engine creation extracted to make_engine in agent/store.py).
- agent/workspace.py: PatchEntry carries patch_id, pd_url, revision and a touched flag; artifacts carry id, revision and pd_url; durable snapshots keep them.
- agent/tools/patch.py: new list_patches and read_patch tools; write_patch accepts the stored id and edits that patch in place; every patch tool resolves by id or name; the 4-patch cap became a 12-patch conversation cap; interface policies include the new tools.
- agent/prompt.py: rules to list and read the existing patch before changing it instead of writing a near-duplicate.
- agent/chat.py: seeds the workspace from the session before the loop, repairs and saves only the patches written in this turn, emits only those as artifacts, and adopts cached patches into the asking session without clobbering a newer revision.
- agent/durable.py: checkpointed _step_load_session_patches seeds the durable workspace; _step_finish_run saves it.
- main.py: GET /sessions/{session_id}/patches and GET /sessions/{session_id}/patches/{patch_id}.pd; the store is wired into /chat and default_runtime.
- Frontend: each patch card shows its revision and downloads the latest .pd by id; a "Patches in this session" strip lists every session patch with a download button.
Validation so far: 18 new tests in tests/test_session_patches.py, a two-run durable test, updated existing tests; frontend build and node --test pass.

Final validation:
- Backend: ./venv/bin/python -m pytest -m 'not slow' -q -> 713 passed, 17 skipped (Pd not installed), 4 deselected.
- New coverage: tests/test_session_patches.py (19 tests: store ids/revisions/prune, seeding and save-back, id resolution in tools, two-request follow-up edit, untouched patches not redrawn, cache adoption without clobbering, list/download API) and tests/test_durable_runs.py::TestSessionPatchWorkspace (two durable runs in one session edit one stored patch).
- Frontend: npm run build OK; node --experimental-strip-types --test src/lib/*.test.ts -> 8 passed (including the new apiUrl test). tsc reports only the pre-existing scroll-area/Node-test-type errors; no new source errors (checked by filtering those).
- Live check: two real requests in one session (real classifier, retrieval and model). Turn 1 wrote patch 'sine-440' (id p62eaf970f23). Turn 2 called list_patches -> read_patch(id) -> write_patch(id), producing revision 2 with the same id and exactly one stored patch. GET /sessions/live-check-1/patches returned that id at revision 2, and its pd_url returned the 220 Hz .pd file with filename="sine-440.pd".
- Note: the download button and session patch strip were not clicked in a browser in this environment; the endpoint, artifact pd_url, URL prefixing and frontend build were verified instead.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a per-session patch workspace. Patches are stored in the app database (session_patches: stable short id, name, JSON, revision) and every request seeds its PatchWorkspace from the session and saves back the patches it wrote. New list_patches/read_patch tools and an id-aware write_patch let a follow-up load and revise the existing patch instead of starting over. GET /sessions/{session_id}/patches lists the patches and GET /sessions/{session_id}/patches/{patch_id}.pd serves the latest revision; the UI shows revisions, downloads by id and lists every session patch. Wired into both the streaming and DBOS-durable paths. Verified with 19 new tests, a two-run durable test, the full suite (713 passed, 17 skipped), frontend build/unit tests, and a live two-turn run where the model called list_patches -> read_patch -> write_patch and kept one patch at revision 2, whose .pd download returned the edited version.
<!-- SECTION:FINAL_SUMMARY:END -->
