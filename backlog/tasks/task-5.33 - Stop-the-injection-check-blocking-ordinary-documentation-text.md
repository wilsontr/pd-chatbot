---
id: TASK-5.33
title: Stop the injection check blocking ordinary documentation text
status: Done
assignee:
  - '@claude'
created_date: '2026-11-27 21:20'
updated_date: '2026-11-27 23:14'
labels:
  - agent
  - security
dependencies: []
references:
  - agent/guardrails.py
  - tests/test_agent_guardrails.py
parent_task_id: TASK-5
priority: low
type: bug
ordinal: 43000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The prompt-injection patterns in agent/guardrails.py flag ordinary text: "If you are not certain, check the help file" matches because the "you are ... (assistant|...|ai)" pattern has no word boundary and "certain" contains "ai", and "the system message box" matches the system-prompt pattern. No current doc chunk trips them (0 of 2,503), but new documentation or a patch's `[print]` output could, and a match blocks the whole tool result. Found in the TASK-5 epic review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Ordinary Pd documentation phrasing (including the two examples above) is not flagged
- [x] #2 The existing injection test cases are still flagged
- [x] #3 A test runs the check over the whole indexed corpus and expects no matches
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Anchor every injection pattern on word boundaries.
2. Make the role pattern require an article and a role noun as a whole word within one sentence.
3. Split the system pattern: 'system prompt' always; 'system message/instructions' only when addressed to the model (your ...) or asked for (show/reveal/print ... ), and not when followed by a UI noun such as box or window.
4. Tests: ordinary Pd phrasing passes, existing injection cases and new role/system cases still match, and the whole indexed corpus (parent and child chunks) passes check_tool_output_injection with no skip.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
agent/guardrails.py: the 'you are ... ai' pattern now needs 'you are [now|no longer] a/an' then up to three words and a whole-word role noun within one sentence, so 'If you are not certain' no longer matches through 'certain'. 'system prompt' still always matches; 'system message/instructions' matches after 'your', or after show/reveal/print/repeat/tell/give/output/display within 20 characters unless followed by box/window/bus/queue/dialog. All patterns gained \b anchors.

Validation: tests/test_guardrails.py adds eight ordinary phrasings (including both review examples), which fail on the old patterns, plus new role/system positive cases; all existing positives pass. tests/test_agent_guardrails.py now checks parent_chunks.json (566) and child_chunks.json (2,503) through check_tool_output_injection, with no skip when the file is missing (both are tracked): 0 matches.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Narrowed the prompt-injection patterns so ordinary documentation and [print] output are not blocked: word boundaries everywhere, a role pattern that needs an actual role assignment, and 'system message' only when addressed to or requested from the model. Verified by new ordinary-phrasing tests (failing on the old patterns), the unchanged injection cases, and a corpus test over all 566 parent and 2,503 child chunks with no matches.
<!-- SECTION:FINAL_SUMMARY:END -->
