---
id: TASK-10
title: Stop the Evaluation workflow failing when main moves mid-run
status: Done
assignee:
  - '@claude'
created_date: '2026-11-25 02:44'
updated_date: '2026-11-25 04:04'
labels:
  - infra
dependencies: []
references:
  - .github/workflows/eval.yml
priority: medium
type: bug
ordinal: 36000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Evaluation runs 45527627711 and 45527867182 passed their quality gate, then failed pushing docs/EVAL.md because main had moved during the run. Pytest comes after that step, so it was skipped too.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Pytest runs before results are published
- [x] #2 A push to main during a run does not fail the run, and results are never published on top of code they did not measure
- [x] #3 A genuine push failure still fails the run
- [x] #4 Pushes to main that change only Backlog or Markdown files do not start a paid evaluation
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Move pytest before publishing. 2. Replace the auto-commit action with a script that publishes on the current tip only if nothing but docs changed since the measured commit. 3. paths-ignore on push. 4. Exercise the script against a local bare repo.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Publish script exercised against a local bare repo, 10 cases: plain, Markdown/Backlog/deep-Markdown moved, code moved, code then Markdown moved, unchanged, unreachable remote (fails), rejected push (fails), and a push landing between fetch and push (retries). Not yet run on GitHub: needs a push. Acceptance criteria stay unchecked until then.

GitHub run 45535349267 on 3fdb54c: pytest ran before publishing (605 passed, 64 skipped) and the script published 8bc441e. AC 2 and 3 rest on the local bare-repo cases. AC 4 needs a Backlog-only push.

Backlog-only push cd9d7a7 (no [skip ci]) started no workflow run within 90 s.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Evaluation runs pytest before publishing, publishes EVAL.md safely when main moves, and ignores Backlog/Markdown-only pushes. Verified by GitHub run 45535349267, a Backlog-only push that started no run, and 10 local race and failure cases.
<!-- SECTION:FINAL_SUMMARY:END -->
