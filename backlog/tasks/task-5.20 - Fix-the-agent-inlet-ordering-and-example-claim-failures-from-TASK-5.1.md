---
id: TASK-5.20
title: Fix the agent inlet-ordering and example-claim failures from TASK-5.1
status: To Do
assignee: []
created_date: '2026-11-24 09:09'
labels:
  - agent
dependencies: []
references:
  - agent/prompt.py
  - agent/tools/docs.py
  - evaluation/golden_dataset.json
  - docs/EVAL.md
parent_task_id: TASK-5
priority: medium
type: bug
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-5.1's pipeline-vs-agent generation eval found two reproducible answer-quality failures that the pipeline arm did not have on the same entries. They are the kind of error the agent's freedom introduces, so they need to be fixed and pinned with regression coverage.

**1. `[line~]` example (golden entry `q_004`)** — "How does [line~] work and what's the gotcha with its right inlet?" The agent's answer included "send the time and the target together, for example a list `100 500`", which implies right-to-left distribution across the inlets. Pd fills list inlets left to right, so that example is wrong. The judge scored faithfulness 2 ("Critical error: list order example contradicts documented inlet distribution"). The rest of the answer (the right-inlet-does-not-remember gotcha) was correct.

**2. `[unpack]` misattribution (golden entry `q_007`)** — "How do [pack] and [unpack] work for assembling compound messages?" The agent retrieved "Chapter 2 > 2.8 Subpatches" and stretched it to support a claim that `[unpack]`'s outlets appear right to left, then cited it. Judge: faithfulness 2, citation 2. The tool-guidance and attribution rules added during TASK-5.1 fixed this case in the re-run, but nothing pins it against recurrence.

Context:
- The agent prompt is `agent/prompt.py` (`AGENT_SYSTEM_PROMPT` = shipped prompt + `_TOOL_RULES`); the loop and tool are `agent/loop.py` and `agent/tools/docs.py`.
- Reproduce an entry with `python -m evaluation.evaluate --generation --mode agent --sample 10`, or by draining `agent.chat.answer_question` for the one question and reading `loop.state.answer` plus the tool calls.
- Golden entries carry `reference_answer` and `relevant_chunk_ids`; `q_004`'s reference answer states the correct list behaviour, which is what the answer should be checked against.

Outcome: the reproducible failures are fixed (prompt, tool description or a deterministic check — decide from the evidence), both cases are covered by the eval so a recurrence shows up in CI, and the report states the re-measured scores. If a failure turns out to be model behaviour that cannot be fixed cheaply, record that finding on the task and pin the case as a known limitation with an explicit threshold decision.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `q_004` is reproduced from current code with the command and answer recorded on the task, and the cause (prompt, tool schema or model behaviour) is identified
- [ ] #2 The `[line~]` answer no longer makes the right-to-left list claim, and that entry scores faithfulness >= 4 in the agent eval
- [ ] #3 Regression coverage exists for both `q_004` and `q_007`, so a recurrence fails or is flagged by the eval rather than being noticed by hand
- [ ] #4 The 10-entry both-arm eval is re-run, the agent numbers are recorded on the task, and the pipeline gate still passes
- [ ] #5 Automated tests cover any new deterministic check or prompt-contract assertion
<!-- AC:END -->
