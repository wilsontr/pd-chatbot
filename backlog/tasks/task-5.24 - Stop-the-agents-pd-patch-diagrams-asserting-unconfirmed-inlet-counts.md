---
id: TASK-5.24
title: Stop the agent's pd-patch diagrams asserting unconfirmed inlet counts
status: To Do
assignee: []
created_date: '2026-11-24 18:31'
labels:
  - agent
dependencies: []
references:
  - agent/patches.py
  - agent/prompt.py
  - evaluation/probe.py
  - evaluation/claims.py
  - evaluation/golden_dataset.json
parent_task_id: TASK-5
priority: medium
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.23 fixed the agent's unsupported outlet-order claim and the `[snapshot~]` prose arrangement, but a residual defect remains: the agent still writes inlet counts into pd-patch blocks that its own excerpts do not state, and sometimes contradicts its own caveat in the same answer.

Measured while implementing TASK-5.23 (5 runs per entry, final prompt, judge `claude-haiku-4-5`): on `q_006`, 4 of 5 answers emitted a `[snapshot~]` object with `"inlets": 2` in the pd-patch JSON, although the IEM reference lists a single inlet row ("1st | bang ... | set <float>") and the prose in the same answer said the diagram's second inlet is not documented. The judge only scored one of those runs 2, so the eval under-reports the error rate. On `q_014`, 1 of 5 answers said "two inlets" in prose.

Why this is separate from TASK-5.23: prompt text measurably stopped the outlet-order claim (2/3 warnings before, 0/5 after) but does not stop the diagram from carrying a count the answer admits it cannot confirm. The durable fix is at the patch boundary — validate or omit counts the excerpts do not support — which needs its own design, measurement and regression run. TASK-5.23 records this residual and tracks it here.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The q_006 `inlets: 2` diagram error is reproduced in N>=3 recorded runs with its rate stated, including the answers whose prose disclaims the count
- [ ] #2 A fix is chosen from evidence (pd-patch validation/canonicalisation or a prompt rule) with before/after rates on q_006 and q_014
- [ ] #3 After the fix, q_006 faithfulness is >= 4 on N>=3 runs and no `[snapshot~]` pd-patch object carries an unconfirmed inlet count
- [ ] #4 The 30-entry both-arm eval still shows 0 claim-check errors and no regression against the TASK-5.23 numbers
- [ ] #5 The new rule or validation is covered by tests
<!-- AC:END -->
