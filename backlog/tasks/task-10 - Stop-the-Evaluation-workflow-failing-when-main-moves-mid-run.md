---
id: TASK-10
title: Stop the Evaluation workflow failing when main moves mid-run
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25 02:44'
updated_date: '2026-11-25 02:45'
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
- [ ] #1 Pytest runs before results are published
- [ ] #2 A push to main during a run does not fail the run, and results are never published on top of code they did not measure
- [ ] #3 A genuine push failure still fails the run
- [ ] #4 Pushes to main that change only Backlog or Markdown files do not start a paid evaluation
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Move pytest before publishing. 2. Replace the auto-commit action with a script that publishes on the current tip only if nothing but docs changed since the measured commit. 3. paths-ignore on push. 4. Exercise the script against a local bare repo.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Publish script exercised against a local bare repo, 10 cases: plain, Markdown/Backlog/deep-Markdown moved, code moved, code then Markdown moved, unchanged, unreachable remote (fails), rejected push (fails), and a push landing between fetch and push (retries). Not yet run on GitHub: needs a push. Acceptance criteria stay unchecked until then.
<!-- SECTION:NOTES:END -->
