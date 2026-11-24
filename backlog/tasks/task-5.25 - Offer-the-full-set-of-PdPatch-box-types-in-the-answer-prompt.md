---
id: TASK-5.25
title: Offer the full set of PdPatch box types in the answer prompt
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24 22:04'
updated_date: '2026-11-24 22:14'
labels:
  - agent
dependencies:
  - TASK-5.2
references:
  - agent/prompt.py
parent_task_id: TASK-5
priority: medium
type: enhancement
ordinal: 34000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.2 widened the PdPatch schema, but SYSTEM_PROMPT still lists only obj, msg, floatatom and comment, so the model does not know it may emit the widget and data-store types.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The answer prompt names every PdPatch box type
- [x] #2 A test fails if the prompt omits a schema type
- [x] #3 Generation evals are re-run on both arms and compared with the TASK-5.24 baseline
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Describe the new types and their counts in SYSTEM_PROMPT (the corpus has no widget counts). 2. Add a test. 3. Run pytest and a one-trial 30-entry eval on both arms; probe real widget questions.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
First wording made the model put hsl's full argument list in text; the final wording asks for the bare type name. Eval on the final prompt (30 entries, 1 trial, $0.6872, gate passed, 0 claim checks flagged; faithfulness/relevance/citation): pipeline 4.53/4.77/4.70 vs 4.63/4.81/4.50 baseline; agent 4.77/4.90/4.57 vs 4.61/4.86/4.48. The baseline is 3 trials, so small differences are not distinguishable from noise. docs/EVAL.md not regenerated (single trial). Ad hoc agent answers used tgl, bng, hsl and table; one run gave table 1 inlet despite the prompt saying 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
SYSTEM_PROMPT (and the agent prompt built on it) now offers all 15 box types. Verified with prompt tests (397 passing), three live agent questions, and a passing one-trial eval of both arms.
<!-- SECTION:FINAL_SUMMARY:END -->
