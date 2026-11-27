---
id: TASK-5.29
title: Stop exposing other users' runs and session patches
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-27 21:40'
labels:
  - agent
  - security
dependencies: []
references:
  - main.py
  - agent/chat.py
  - agent/store.py
  - frontend/src/lib/auth.ts
parent_task_id: TASK-5
priority: high
type: bug
ordinal: 39000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The API key is shipped in the browser bundle (`VITE_CHAT_API_KEY`, frontend/src/lib/auth.ts), so anyone can call the API. `GET /runs` without `session_id` lists every user's runs and `GET /runs/{run_id}` returns full event histories (questions, answers, tool calls). Those listings expose session ids, and `/chat` accepts any client-supplied `session_id` (agent/chat.py), so a caller can read another session's patches via `GET /sessions/{id}/patches` or read and overwrite them through the agent's `read_patch` / `write_patch` tools. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A caller cannot list or read runs that belong to a session it did not create
- [x] #2 A session id the server did not issue to the caller is rejected by `/chat`, `/sessions/{id}/patches` and the `.pd` download
- [x] #3 The run timeline and run list pages still work for the caller's own runs
- [x] #4 Tests cover cross-session access to runs, patches and the chat workspace
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add agent/session_tokens.py: the server mints session ids as <uuid>.<HMAC-SHA256> under SESSION_SECRET (comma-separated for rotation; a random per-process secret with a warning when unset) and verifies them in constant time.
2. main.py: /chat and /runs/durable mint a token when none is sent and reject an unverified one (403 Unknown session); /sessions/{id}/patches and the .pd download 404 on an unverified id.
3. Runs: GET /runs requires a verified session_id; GET /runs/{id} requires the run's own session_id (query) and 404s otherwise. An operator key (ADMIN_API_KEYS, sent as X-Admin-Key, never shipped to the browser and not allowed by CORS) can list and read every run.
4. Frontend: keep the session token in sessionStorage, list the current session's runs by default, and pass the token when fetching a run.
5. Tests: cross-session access to runs, run detail, session patches, .pd download and the chat workspace; forged and unsigned session ids; admin access. Update the existing API tests to use minted ids.
6. Document SESSION_SECRET and ADMIN_API_KEYS in README/AGENTIC docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design: session ids are capabilities. agent/session_tokens.py mints <32 hex>.<HMAC-SHA256> under SESSION_SECRET (comma-separated; first signs, any verifies, so rotation keeps live sessions; unset means a per-process secret with a warning). main.py mints when no id is sent and refuses unsigned ids: /chat and /runs/durable 403, session patch list and .pd download 404, GET /runs 403 without a verified session_id, GET /runs/{id} 404 unless ?session_id= is the run's own (compare_digest). Operator keys (ADMIN_API_KEYS, header X-Admin-Key) read every run; CORS allow_headers excludes the header so pages cannot send it. Enforcement is at the HTTP layer only: the CLI and MCP server use the stores directly and are unchanged. Frontend keeps the id in localStorage (pd-chatbot.session-id) so a reload or a new tab can replay the session's runs; fetchRun/fetchRuns send it.

Validation:
- pytest tests: 807 passed, 87 skipped (sandbox image not available, same as before). New: tests/test_session_tokens.py (minting, forged/tampered/foreign-secret ids, rotation), TestSessionOwnership in tests/test_run_store.py (server-issued session, forged ids refused at /chat before any run or workspace is touched, listing scoped, another session's run 404s, operator key, CORS refuses X-Admin-Key), tests/test_session_patches.py (forged ids and another session's id open no patches or .pd).
- Frontend: vite build OK; node --test 8 pass; tsc/eslint show only the 7 errors already on main (node types in *.test.ts, scroll-area React import, frontend/App.jsx).
- Browser (local backend on 8011 + Vite on 5181, real model call): the first answer stored a signed id; Runs page listed the run; the timeline opened. From that page: GET /runs without session 403, run without session 404, with session 200, forged session patches 404, /chat with forged session 403. After clearing localStorage the run reads as not found and the Runs page shows 'No runs in this session yet'.
- Not done: no migration for runs stored under old unsigned ids (they become operator-only), and the page does not recover by itself if SESSION_SECRET changes (old ids then 403 on /chat until localStorage is cleared).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Session ids are now server-signed capabilities. The server mints <random>.<HMAC> ids under SESSION_SECRET, refuses ids it did not sign, and opens a session's runs and patches only with its own id; another session's run answers 404 like a missing one. Operators read every run with ADMIN_API_KEYS via an X-Admin-Key header that CORS keeps away from pages. The browser stores its id in localStorage and sends it with run requests. Verified by new pytest cases (token unit tests, cross-session runs/patches/chat, operator access, CORS), the full suite (807 passed, 87 sandbox skips), and a browser check against a local backend. Deploy needs SESSION_SECRET set; ADMIN_API_KEYS is optional.
<!-- SECTION:FINAL_SUMMARY:END -->
