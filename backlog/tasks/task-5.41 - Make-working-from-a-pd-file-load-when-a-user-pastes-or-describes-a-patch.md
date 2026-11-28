---
id: TASK-5.41
title: Make working-from-a-pd-file load when a user pastes or describes a patch
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-28 02:47'
updated_date: '2026-11-28 04:42'
labels:
  - agent
  - skills
dependencies: []
parent_task_id: TASK-5
priority: medium
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The skill is almost never selected: load rate 9% (4/45) in CI run 46095333752, against a 50% floor; every pasted-canvas question except pd-file-001 loaded 0/3. Answers are mostly fine without it (faithfulness 4.32, relevance 4.76), so the failure is the description, not the content. Two golden questions are unanswerable in every trial: pd-file-003 (asks to rebuild a subpatch it never shows) and pd-file-013 (refers to a patch 'pasted earlier' with no history), both no_context 3/3, so they count as misses whatever the skill does. Evidence: skill-eval artifact of CI run 46095333752 (15+ questions x 3 trials, per-question scores and loaded_skill). Found in TASK-5.37.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The skill's description makes the model call load_skill for pasted #N canvas text and for questions about reading or changing an existing .pd file
- [ ] #2 pd-file-003 and pd-file-013 are replaced or given the patch text they need, so no golden question is unanswerable by construction
- [ ] #3 The skill passes the CI gate (skills.yml, 3 trials) and its activation carries CI evidence
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Measure the load rate locally (load only, no judge) to reproduce CI.
2. Rewrite the description as a trigger: Pd file text (#N canvas, #X obj/msg/connect) or the user's own .pd file/patch.
3. Give pd-file-003 and pd-file-013 the patch text they ask about.
4. Re-measure load rate and false triggers on other skills' questions.
5. Gate in CI (skills.yml, 3 trials).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Local load-rate check (answer only, 15 questions x 2): old description 2/30 (7%, CI saw 9%); new description 20/30 (67%). Still missed: pd-file-008, 009, 013, 014 (0/2). False triggers: 18 questions from dsp-debugging, abstractions-and-dollar-zero and granular-synthesis answered with only this skill installed loaded it 0/18 with both the old and new description. pd-file-003 now includes a subpatch to flatten and pd-file-013 a 10-object patch to isolate the envelope from (both were no_context 3/3).

CI gate 46105148152 (15 x 3): load rate 62% (fixed), faithfulness 4.14, relevance 4.67, citation 3.93 -> fail. The judge's citation complaints were file-format answers (#X connect, #X restore, floatatom) citing unrelated manual sections; the index has no chunk documenting the .pd format (0 of 566 parent chunks contain #N canvas or #X connect). Added a Citations section to SKILL.md: file-format facts are stated uncited with a one-line note that the docs do not cover the format; cite excerpts only for object behaviour; a saved subpatch is cited from the Subpatches section. Local 15 x 2 judged run after the first part of that change: citation 4.04, faithfulness 4.21, relevance 4.82, load 63%.

CI re-gate 46106340591 after the Citations section: citation 3.77, faithfulness 4.08, relevance 4.87, load 51% -> fail (local 15 x 2 had shown 4.04). The low citations are now structural: pd-file-006 (what '#X connect 2 0 4 1' means) scores 1-2 in every trial even when the answer cites nothing and says the docs do not cover the format - the judge rates claims with no supporting excerpt as unsupported, and no excerpt can support a file-format claim because the index has none. pd-file-014 (floatatom record) does the same without the skill loaded (0/3). The load-rate criterion is met (51-67%), but the gate cannot pass on file-format questions until the format is in the indexed docs or the golden set stops asking about it. Blocked on that decision (user).

Indexed the file format (user decision). No official source exists: the Pd manual (msp.ucsd.edu and the upstream doc/1.manual) never documents the .pd format. Added the community 'Unofficial PD v0.37 fileformat specification' (Asselbergs and Sietsma, 2004, GNU FDL 1.2, puredata.info/docs/developer/PdFileFormat) as sources/pd_file_format.json, 18 sections (GUI widget records and the EBNF appendix omitted), with its licence notice and modification note in sources/README.md; the licence text itself is linked, as on the original page, not copied. parse_file_format.py merges it idempotently into the committed chunk files (18 parent, 56 child chunks, additions only) and chunk.py includes it in a full rebuild. Excerpts are labelled per source (agent/retrieval.py SOURCE_LABELS; this also fixes the Puckette book being labelled 'MSP Manual'). The skill's Citations section now cites the spec, says it is unofficial and for Pd 0.37, and keeps object behaviour on the manual/reference.
Validation: retrieval eval unchanged (hybrid recall@5 75.00%, MRR 0.696); non-sandbox suite 876 passed; tests/test_file_format_source.py. Local judged run 15 x 2: citation 4.57, faithfulness 4.54, relevance 4.89, load 63% (pd-file-006 went from 1-2 to 5/5).
<!-- SECTION:NOTES:END -->
