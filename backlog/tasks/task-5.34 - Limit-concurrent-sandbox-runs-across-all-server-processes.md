---
id: TASK-5.34
title: Limit concurrent sandbox runs across all server processes
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-27 23:21'
labels:
  - agent
  - sandbox
dependencies: []
references:
  - sandbox/runner.py
  - docs/SANDBOX.md
parent_task_id: TASK-5
priority: low
type: enhancement
ordinal: 44000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`PD_SANDBOX_CONCURRENCY` is enforced by a threading semaphore in sandbox/runner.py, so with several server workers the real limit is workers times the setting, and Modal spend scales with it. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The configured limit holds across all worker processes of one deployment, or the docs state the per-process behaviour and the deployment sets it accordingly
- [x] #2 docs/SANDBOX.md describes the behaviour
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add sandbox/slots.py: a run holds a per-process semaphore slot and, when a Postgres URL is configured (PD_SANDBOX_LOCK_URL, else DATABASE_URL), one of PD_SANDBOX_CONCURRENCY Postgres session advisory locks, so the limit holds across every process and replica sharing that database. Locks die with their connection, so a crashed process frees its slot.
2. If Postgres cannot be reached, log a warning and fall back to the per-process limit rather than failing the run.
3. sandbox/runner.py uses it in place of the bare semaphore.
4. Tests: slot logic with a fake connection; with TEST_DATABASE_URL, two holders contend for one slot and a killed process's slot is freed. Run those locally against a scratch Postgres.
5. docs/SANDBOX.md describes the deployment-wide limit, the fallback and the variables.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
sandbox/slots.py: run_slot(timeout) takes the per-process BoundedSemaphore, then, when PD_SANDBOX_LOCK_URL or DATABASE_URL is Postgres, one of PD_SANDBOX_CONCURRENCY session advisory locks (pg_try_advisory_lock(0x50645362, slot), polled every 0.2 s from a random start slot) on its own autocommit psycopg connection, held for the run. Unreachable database: warning and per-process limit only. sandbox/runner.py run_patch uses it; the 60 s busy timeout is unchanged. Production (one uvicorn worker, one replica, DATABASE_URL = Railway Postgres per README) gets the deployment-wide limit with no config change. Cost: one extra short-lived Postgres connection per sandbox run.

Validation: tests/test_sandbox_slots.py (fake-lock slot logic, timeout, hand-over, unreachable fallback, URL handling) passes; its two Postgres tests (two sessions contend for one slot; a SIGKILLed child process frees its slot) passed against a scratch local Postgres via TEST_DATABASE_URL and are skipped without it, as in CI. Full suite: 907 passed, 19 skipped. Unrelated: tests/test_run_store.py's Postgres variant errors in fixture setup against that scratch cluster (SQLAlchemy server-version parse got bytes, SQL_ASCII initdb), not from this change.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
PD_SANDBOX_CONCURRENCY now holds across every worker process and replica that shares a Postgres database: each sandbox run holds one of that many Postgres advisory locks besides its per-process slot, a killed process frees its slot with its connection, and an unreachable database falls back to the per-process limit with a warning. docs/SANDBOX.md has a Concurrency section and the new PD_SANDBOX_LOCK_URL variable. Verified by new slot tests, including two run against a real local Postgres (contention, killed-process release), and the full suite (907 passed, 19 skipped).
<!-- SECTION:FINAL_SUMMARY:END -->
