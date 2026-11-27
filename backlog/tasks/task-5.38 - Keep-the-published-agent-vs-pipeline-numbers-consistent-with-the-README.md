---
id: TASK-5.38
title: Keep the published agent-vs-pipeline numbers consistent with the README
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - eval
  - docs
dependencies: []
references:
  - docs/EVAL.md
  - README.md
  - .github/scripts/publish_eval.sh
  - .github/workflows/eval.yml
parent_task_id: TASK-5
priority: medium
type: docs
ordinal: 48000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every push to main regenerates docs/EVAL.md from a 10-entry, 1-trial sample, replacing the 30-entry, 3-trial baseline from TASK-5.21. The current report shows the agent behind the pipeline (task success 80% vs 90%, citation 4.50 vs 5.00) on too few runs to mean much, while the README says the agent is level with the pipeline and that EVAL.md holds the mean and standard deviation. The epic's acceptance criterion #2 depends on this report. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The agent-vs-pipeline comparison the README cites is the multi-trial baseline, and a routine push does not overwrite it
- [ ] #2 Each published table states its sample size and number of trials
- [ ] #3 README figures match the published report they link to
<!-- AC:END -->
