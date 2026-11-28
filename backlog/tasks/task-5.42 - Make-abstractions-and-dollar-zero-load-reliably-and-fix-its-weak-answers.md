---
id: TASK-5.42
title: Make abstractions-and-dollar-zero load reliably and fix its weak answers
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-28 02:48'
updated_date: '2026-11-28 05:25'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Load rate 44% in CI run 46095333752 (floor 50%); six questions loaded 0/3 or 1/3, including abs-001 (two copies share a receive value) and abs-007/abs-011/abs-015, which are squarely the skill's topic. Scores otherwise clear the floors (faithfulness 4.53, relevance 4.87, citation 4.29), but abs-010 (editing an abstraction while instances are open) and abs-012 (abstraction not found on the path) score citation 2 in every trial, and abs-004 scores relevance 2-4. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The skill is loaded for at least half of its golden slice in a 3-trial CI run
- [ ] #2 abs-010 and abs-012 answers cite excerpts that support them, or the skill says the docs do not cover the point
- [x] #3 The skill passes the CI gate and its activation carries CI evidence
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rewrite the description as a trigger listing the user situations it covers.
2. Add a 'Where the docs say it' section mapping each point to the excerpt that supports it (2.7.6, 2.8, 2.8.1, 2.8.2, 3.4, 4.4.2, clone and pdcontrol pages).
3. Remove the skill's own unsupported claim about open instances not updating.
4. Measure load rate and false triggers locally, then gate in CI.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Judge reasons (CI run 46095333752): abs-010 and abs-012 cited sections or URLs that did not support them (6.5 building-from-source for a path problem); abs-010 answers also contradicted the docs on whether open instances update, which the skill itself asserted in two places. Manual 2.8.1 says saved changes 'affect all invocations of it as they are created'; 3.4 says the search paths load externals and abstractions; [pdcontrol] (args) and [clone] pages are indexed.
Local, 15 x 2: load rate old description 43%, new 60%; false triggers on 18 questions from other skills 0/18. Judged 15 x 2 with the new description and anchors: faithfulness 4.47, relevance 4.80, citation 4.20, load 67% (abs-010 and abs-012 still citation 2-3, hence step 3).

CI gate 46111406363 passed and activated (ec99fa2): faithfulness 4.69, relevance 4.84, citation 4.09, load 78% (was 44%). abs-010 citation 2/4/4 and abs-012 2/5/4 across the three trials (were 2/2/2 and 2/2/2): better, not fixed. In trial 1 abs-012 still cited section 6.5 (macOS build) for a path problem, and abs-010 cited a section on persistence. AC2 left unchecked; AC1 and AC3 verified by this run.
<!-- SECTION:NOTES:END -->
