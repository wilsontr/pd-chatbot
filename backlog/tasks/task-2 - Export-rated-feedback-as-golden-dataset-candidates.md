---
id: TASK-2
title: Export rated feedback as golden-dataset candidates
status: Done
assignee:
  - '@deepseek'
created_date: '2026-11-23 20:39'
updated_date: '2026-11-23 22:12'
labels:
  - feedback
  - evaluation
dependencies:
  - TASK-1
references:
  - main.py
  - evaluation/dataset.py
priority: medium
type: feature
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Close the loop between production usage and offline evaluation. The frontend already collects thumbs up/down and the backend persists them, but nothing turns that data into test cases. Downvoted answers are the most valuable source of new hard eval questions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A CLI command exports feedback rows (filterable by rating and date range) in the same JSON shape as `evaluation/golden_dataset*.json`, with source sections pre-filled from the stored chunks
- [x] #2 Exported candidates are written to a separate review file and never added to the golden set automatically
- [x] #3 The README evaluation section explains the feedback-to-eval workflow
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add filtered reads to the feedback store (rating, date range) without changing existing behaviour; isolate the SQL in feedback_store.py so the CLI stays thin.
2. New module evaluation/feedback_export.py (stdlib + feedback_store only, no rag/chroma/API keys): resolves each stored source back to its parent chunk id via the unique (heading_path, url, source) key in parent_lookup.json, then emits GoldenEntry-shaped dicts (id, question, history, query_type, relevant_chunk_ids, reference_answer, difficulty, notes) with provenance (rating, comment, sources) in notes.
3. CLI: python -m evaluation.export_feedback --rating up|down|both --since --until --output --database-url --parent-lookup. Default output evaluation/feedback_candidates.json. Refuse to write any golden_dataset*.json path; the writer only ever creates the review file.
4. Tests in tests/test_feedback_export.py: rating/date filters, golden-shape key parity with GoldenEntry.to_dict, id resolution from sources, unresolvable sources noted, no-automatic-golden-set guard, empty result.
5. README: document the feedback -> candidate review -> golden dataset workflow, including the DB access caveat (no public Postgres proxy, run via railway run).
6. Gitignore the review file so user-derived questions are not committed; add a short note next to the existing feedback.db entry.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-11-23:
- evaluation/feedback_export.py (standard library + feedback_store only, so it runs without the Chroma index or API keys): select_rows filters by rating and UTC day range; build_candidate emits a GoldenEntry through GoldenEntry.to_dict() so the shape cannot drift from evaluation/dataset.py; sources resolve to parent chunk ids via the (heading_path, url, source) triple, which is unique across all 566 entries in parent_lookup.json.
- CLI defaults to evaluation/feedback_candidates.json and refuses any golden_dataset*.json output path (exit 2). The writer replaces the file atomically.
- feedback_store.get_feedback() now also selects ratings.created_at as rated_at: the server insert time is the trustworthy date, since ratings.timestamp is the client clock inside the 60s replay window. Filtering happens in Python because SQLite's server_default values are naive while Postgres returns aware datetimes, so a SQL date comparison would be dialect-dependent at the boundary.
- reference_answer is deliberately empty (a good answer is what a reviewer writes); a downvote is tagged difficulty=hard, an upvote medium; notes carry rating, comment, retrieved sources, the stored answer and message_id for provenance. Unresolvable sources are left out of relevant_chunk_ids and called out in notes instead of being guessed.
- The review file is gitignored because it contains user questions.

Verification:
- tests/test_feedback_export.py: 26 passed (rating filter, inclusive since / exclusive until, naive SQLite timestamps treated as UTC, golden-shape key parity with GoldenEntry, real parent_lookup resolution for every parent chunk, exported file loads through GoldenDataset, empty selection, golden-path refusal, CLI via main() with real args). tests/test_feedback.py: 9 passed. Both suites green together (35 passed).
- End-to-end against a scratch SQLite DB seeded with real parent chunks: '--rating down' wrote 1 hard candidate with relevant_chunk_ids=['iem_parent_158']; '--rating up' wrote the upvoted row; '--until 2026-11-22' wrote []; '--output evaluation/golden_dataset_new.json' exited 2 and created nothing. An earlier run against a 3-row DB wrote 2 downvoted candidates, both with pre-filled chunk ids, and the file loaded through GoldenDataset.
- git status confirms no golden_dataset*.json changed.

Environment notes (not caused by this change): test_evaluation.py, test_guardrails.py and test_pd_schema.py cannot be collected here because rag.py needs the Chroma index and there is no VOYAGE_API_KEY to build it (CI builds it in eval.yml). Separately, in this agent sandbox 'python -m evaluation.<new module>' intermittently reported 'No module named ...' for freshly written files while 'from evaluation.feedback_export import main' always worked; clearing evaluation/__pycache__ made it stable. Not reproducible from the code and not present in a normal checkout.

Full-suite update after VOYAGE_API_KEY was added to .env:
- Built the local Chroma index with embed_and_index.py (2503 child chunks embedded via voyage-3-lite). parent_lookup.json came out byte-identical, so no generated artifact changed.
- pytest tests/: 72 passed, 4 failed. tests/test_guardrails.py and tests/test_pd_schema.py, which could not even be collected before the index existed, now pass.
- The 4 failures are tests/test_evaluation.py::test_retrieval_{recall,mrr}_threshold and test_hybrid_beats_{vector_only,bm25_only}. They are pre-existing and unrelated to this task: rag.retrieve() classifies the query with claude-haiku-4-5 and there is no heuristic fallback, so with no ANTHROPIC_API_KEY every entry raises an auth error, which run_retrieval_eval swallows, leaving recall 0.0000. Ran the same four tests in a worktree at d33a207 (the commit before this task) against the same index and env: identical 4 failures. They need ANTHROPIC_API_KEY, which CI supplies via secrets.
- Consequence: TASK-2's own evidence stands (35/35 feedback and export tests pass); the retrieval thresholds remain unverified locally until an Anthropic key is available.

Full suite green 2026-11-23 after ANTHROPIC_API_KEY was added to .env: pytest tests/ -> 76 passed in 113s. The four retrieval threshold tests now run for real: hybrid RRF recall@5 0.7667, MRR 0.7556, recall@10 0.8000 (thresholds 0.60 / 0.30); vector-only 0.7278 recall@5 / 0.6106 MRR; BM25-only 0.3667 / 0.2648. Hybrid beats both baselines and the local numbers match the README table. No tracked files changed by the run (the eval was called through the API, not the --report path, so docs/EVAL.md was left to CI).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a one-way, manual path from production feedback to the eval set. 'python -m evaluation.export_feedback' reads the ratings stored by feedback_store, filters by rating (--rating up|down|both) and UTC date range (--since/--until, inclusive days), resolves each stored source back to its parent chunk id through the unique (heading_path, url, source) key in parent_lookup.json, and writes GoldenEntry-shaped candidates to evaluation/feedback_candidates.json.

The review file is separate by design: the writer refuses any golden_dataset*.json path (CLI exits 2), so candidates reach the golden set only by hand. question, query_type and relevant_chunk_ids are pre-filled; reference_answer is left empty for the reviewer; notes carry the rating, comment, retrieved sources, stored answer and message_id, and call out any source that could not be resolved. Downvotes are tagged difficulty=hard, upvotes medium. feedback_store.get_feedback() now also returns the server-side rating time (rated_at), which the export filters on, because the stored rating timestamp is the client clock. The review file is gitignored since it holds user questions, and the README's Evaluation section documents the workflow and the Railway Postgres access caveat.

Verified by 26 new tests in tests/test_feedback_export.py (rating/date filters, timestamp normalisation, shape parity with GoldenEntry, real chunk-id resolution for every parent chunk, exported file loading through GoldenDataset, empty selection, golden-path refusal, CLI invocation) plus the 9 existing feedback tests - 35 passed together. Also exercised end to end against a scratch SQLite DB seeded with real chunks: down-only, up-only, an out-of-range window (empty file) and a golden_dataset output path (refused, nothing written), with git status confirming no golden dataset changed. Committed as bc9a7c5.
<!-- SECTION:FINAL_SUMMARY:END -->
