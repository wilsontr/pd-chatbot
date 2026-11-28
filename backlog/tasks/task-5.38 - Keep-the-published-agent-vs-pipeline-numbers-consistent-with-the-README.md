---
id: TASK-5.38
title: Keep the published agent-vs-pipeline numbers consistent with the README
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-28 01:23'
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
- [x] #1 The agent-vs-pipeline comparison the README cites is the multi-trial baseline, and a routine push does not overwrite it
- [x] #2 Each published table states its sample size and number of trials
- [x] #3 README figures match the published report they link to
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Restore the 30-entry x 3-trial report (commit dc0b8f9, TASK-5.24, the figures the README quotes) as docs/EVAL_BASELINE.md.
2. eval.yml: a run with 3+ trials writes and publishes EVAL_BASELINE.md; every other run writes EVAL.md, so a push cannot overwrite the baseline. publish_eval.sh takes the report path.
3. evaluate._build_report: state sample and trials before the tables, in the retrieval and generation sections and in the pipeline-vs-agent table; a 1-trial report points at the baseline.
4. README and FAILURE_MODES cite EVAL_BASELINE.md; tests/test_readme_eval.py fails when README figures differ from it.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
docs/EVAL_BASELINE.md is the dc0b8f9 report with its numbers unchanged; a header records the commit and command, and the new sample statements were added by hand (re-running 30x3 costs ~$2.10 and ~50 min). The baseline run skips tests/test_readme_eval.py, so a new baseline is published and the next push fails until the README is updated. docs/EVAL.md on main still predates the change; the next push to main regenerates it with the sample lines (the routine-report check skips until then).
Validation: tests/test_readme_eval.py (5 pass, 1 skip until EVAL.md regenerates), new report tests in tests/test_eval_trials.py, tests/test_ci_workflows.py::TestEvalReports, test_agent_eval/test_agent_metrics updated for the docs-mode wording.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The README's agent-vs-pipeline figures now cite docs/EVAL_BASELINE.md, the 30-entry x 3-trial report restored from dc0b8f9. Only a manual eval run with 3+ trials rewrites it; pushes write docs/EVAL.md, which now states it is a 1-trial sample and links to the baseline. Every report table states its sample and trials, and tests/test_readme_eval.py fails if the README's figures stop matching the baseline.
<!-- SECTION:FINAL_SUMMARY:END -->
