---
id: TASK-5.37
title: Gate the baseline skills and check activation on main
status: To Do
assignee: []
created_date: '2026-11-27 21:20'
labels:
  - agent
  - skills
  - eval
dependencies: []
references:
  - skills/activation.json
  - .github/workflows/skills.yml
  - evaluation/skills.py
parent_task_id: TASK-5
priority: medium
type: task
ordinal: 47000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Five of the six skills (abstractions-and-dollar-zero, dsp-debugging, granular-synthesis, pd-flavors, working-from-a-pd-file) are pinned in skills/activation.json as "baseline" and have never passed their own golden-set gate. The only gated skill, wavetables (TASK-5.28), was activated from a local run (actor "local"). skills.yml runs only on pull requests, so a direct push to main changes what is served with no CI check. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every activated skill has gated evidence recorded in skills/activation.json, or is deactivated with a recorded reason
- [ ] #2 wavetables is re-gated in CI and its evidence replaces the local one
- [ ] #3 A check on main fails when skills/activation.json pins a version that has no CI-gated evidence or does not match its snapshot
<!-- AC:END -->
