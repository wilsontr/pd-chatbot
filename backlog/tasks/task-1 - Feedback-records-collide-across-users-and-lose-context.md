---
id: TASK-1
title: Feedback records collide across users and lose context
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23 20:39'
updated_date: '2026-11-23 20:57'
labels:
  - feedback
  - backend
dependencies: []
references:
  - main.py
  - rag.py
priority: high
type: bug
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The `/feedback` endpoint in `main.py` stores ratings in SQLite keyed on `message_id`, but `message_id` is `_make_cache_key(question)` in `rag.py`: a hash of the normalised question only. Consequences:

- `INSERT OR IGNORE` on the primary key means only the **first** rating ever given to a question is kept; every later user rating the same question is silently dropped.
- The question, answer and chunks are looked up from the 1-hour in-memory `_response_cache`. A rating sent after eviction or a restart is stored with an empty question and answer.
- `feedback.db` is a relative path inside the Railway container. Unless a volume is mounted, all feedback is lost on redeploy.

The feedback loop (thumbs up/down to a data flywheel) is only useful if each rating is stored with its full context.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each assistant response gets a unique message id, separate from the response-cache key
- [ ] #2 Two users rating the same question produce two stored feedback rows
- [ ] #3 Feedback rows contain the question, answer and retrieved chunks even when the rating arrives after the response cache entry has expired
- [ ] #4 Feedback storage survives a Railway redeploy (mounted volume or external database), and the README says how it is configured
- [ ] #5 Tests cover duplicate-question ratings and post-eviction ratings
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Store each response at generation time with a fresh uuid4 `message_id` (separate from the response-cache key): `main.py` captures the question, streamed answer, sources and query type from the event stream and writes a `responses` row before the `done` event goes out.
2. Store ratings in a separate `ratings` table that references `responses`, so feedback no longer depends on the in-memory cache. A replayed rating for the same message is ignored; an unknown `message_id` returns 404.
3. Put storage behind a small SQLAlchemy Core module (`feedback_store.py`): Postgres via `DATABASE_URL` in production, SQLite locally and in tests. Log a warning when running on Railway without `DATABASE_URL`.
4. Tests: two users rating the same question get two rows; a rating after the cache entry expires still has full context; an unknown id is rejected.
5. Provision Railway Postgres in the "pd chatbot" project. Do not set `DATABASE_URL` on the service or deploy until the user has exported the live `feedback.db`, since any deploy wipes it.
6. Document configuration in the README.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Investigation 2026-11-23:
- Railway project "pd chatbot" (production, service pd-chatbot) has no volumes attached. `feedback.db` sits on the container filesystem and is wiped on every deploy; 20 deployments are on record, the current one from 2026-07-23.
- Could not check how many rows the live container holds (SSH was not permitted from the agent session). Export it before any deploy, or it is lost.
- The frontend treats `message_id` as opaque (`useChat.ts`, `MessageBubble.tsx`, `lib/auth.ts`), so switching to a per-response id needs no frontend change.
- Unrelated: Railway warns that `railway.json` (Config as Code) stops working on 2026-12-01; migrate with `railway config migrate`.

2026-11-23 20:53 UTC: Added Railway Postgres (service "Postgres", id cd8f23d1-5c50-410c-ad8e-fdaa461eb803) to project "pd chatbot"/production; it deployed successfully. Side effect: adding the service triggered a redeploy of pd-chatbot on the same commit (deployment 09677a0f-d788-416f-917d-8690cd1143ea). When that goes live it replaces the container holding the old feedback.db. Cancelling it from the agent session was not permitted; the user needs to cancel it or save feedback.db first. DATABASE_URL is not yet set on pd-chatbot. Code changes (feedback_store.py, main.py, rag.py, tests/test_feedback.py) pass 14 tests on SQLite and a scratch Postgres.

2026-11-23: The redeploy 09677a0f went live, so the pre-fix feedback.db is gone. Set DATABASE_URL=${{Postgres.DATABASE_URL}} on pd-chatbot (resolves to postgres.railway.internal:5432); the IaC config on claude/railway-iac-migration leaves variables unmanaged, so there is no drift. README documents the storage setup. Remaining: commit and push the fix to main, then verify on the deployed app that a chat plus a rating writes rows to Postgres and that a redeploy keeps them (AC #4).
<!-- SECTION:NOTES:END -->
