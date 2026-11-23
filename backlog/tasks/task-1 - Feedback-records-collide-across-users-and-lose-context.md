---
id: TASK-1
title: Feedback records collide across users and lose context
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23 20:39'
updated_date: '2026-11-23 21:48'
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
- [x] #1 Each assistant response gets a unique message id, separate from the response-cache key
- [x] #2 Two users rating the same question produce two stored feedback rows
- [x] #3 Feedback rows contain the question, answer and retrieved chunks even when the rating arrives after the response cache entry has expired
- [x] #4 Feedback storage survives a Railway redeploy (mounted volume or external database), and the README says how it is configured
- [x] #5 Tests cover duplicate-question ratings and post-eviction ratings
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

2026-11-23 verification after pushing bc82d5e (Railway deployment 487bf799, SUCCESS, /health 200, no "DATABASE_URL is not set" warning in startup logs):
- Live check through https://pd-chatbot.vercel.app: asked "What does the osc~ object do?"; the done event carried message_id 4fc54205982d45da889939f4943cecff (random UUID, not a question hash), the feedback buttons appeared, and POST /api/feedback returned 200, which the new code only returns when the response row exists.
- tests/test_feedback.py: 9 passed on SQLite; 14 passed earlier including a scratch Postgres via TEST_DATABASE_URL.
- AC #4 not yet proven: Postgres has no public proxy, so the rows could not be queried from the agent machine, and no app redeploy has happened since the test rating. Check the Postgres service Data tab in the Railway dashboard for the row above after the next redeploy.
- Unrelated: production startup logs show Langfuse running without LANGFUSE_PUBLIC_KEY, so tracing is disabled in production.

2026-11-23: User confirmed message_id 4fc54205982d45da889939f4943cecff is present in the production Postgres `responses` and `ratings` tables, so writes go to Postgres, not the container. Logs show the test chat (21:11:19) and rating (21:11:49) were served by deployment cd428bf1, which started at 21:10:53, so no app redeploy has happened since the rating. AC #4 needs one more redeploy followed by the same check. Redeploying from the agent session was not permitted.

2026-11-23: Redeploy verified. Deployment 1eacd62a was created at 21:45:15 UTC and the app started at 21:46:48 UTC, replacing cd428bf1 (which served the 21:11:49 test rating). The user then confirmed message_id 4fc54205982d45da889939f4943cecff is still in the Postgres `responses` and `ratings` tables.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Feedback now survives redeploys and keeps one record per response. Every response gets a random message_id and is stored (question, answer, sources, query type) when it finishes streaming. Ratings go in a separate table that references the response, so they no longer depend on the 1-hour in-memory cache. Storage is a Railway Postgres service (DATABASE_URL=${{Postgres.DATABASE_URL}}) through the new `feedback_store.py`, with SQLite as the local fallback. Replayed ratings are ignored and unknown ids return 404. The README documents the setup.

Verified by: tests/test_feedback.py (9 passing on SQLite, 14 including a scratch Postgres); a live chat and rating on https://pd-chatbot.vercel.app (UUID message_id, /feedback 200); the user confirming the row in production Postgres; and the row still being there after redeploy 1eacd62a. The pre-fix feedback.db was lost when adding Postgres triggered a redeploy. Committed in bc82d5e.
<!-- SECTION:FINAL_SUMMARY:END -->
