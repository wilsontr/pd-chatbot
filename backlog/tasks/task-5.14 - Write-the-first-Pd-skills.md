---
id: TASK-5.14
title: Write the first Pd skills
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-26 00:07'
labels:
  - agent
dependencies:
  - TASK-5.13
parent_task_id: TASK-5
priority: low
type: feature
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Author an initial set of domain skills: granular synthesis, Purr Data / Pd-L2Ork differences, debugging DSP problems (denormals, DC offset, `block~`), abstractions and `$0` scoping, and building on a `.pd` file the user uploads.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 At least four skills exist, each with a clear trigger description
- [x] #2 Each skill comes with a small golden set of questions it should improve
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Author five skills under skills/ (at least four are required), each a folder with SKILL.md front matter (name, description, version) and grounded instructions: granular-synthesis, dsp-debugging, abstractions-and-dollar-zero, working-from-a-pd-file, pd-flavors (Purr Data / Pd-L2Ork and other forks). Content is drawn from the corpus (book 'Sampling'/'Enveloping samplers', manual 2.5.4 switching/blocking, 2.7.6 dollar signs, 2.8 subpatches/abstractions, 2.9 arrays, and the IEM object pages) and is written as procedure + verification + pitfalls, never as citable facts.
2. Give every skill a golden set in skills/<name>/golden.json (3-5 questions) in a documented schema: id, question, optional expects (cheap keyword signal), optional notes (what a good answer does). Questions are new, not duplicates of evaluation/golden_dataset*.json, and target the behaviour the skill should improve.
3. Add agent/skills/golden.py to parse and validate golden sets (skill name matches folder, 3-12 unique ids, non-empty questions, non-empty expects), raising SkillError so the format cannot rot; it is the loader TASK-5.15's eval gate will use.
4. Document the golden-set format in skills/README.md and note the five skills in README.
5. Tests: loader unit tests plus a shipped-content test asserting >=4 skills, every skill has a valid golden set with unique ids, the registry loads the repo skills/ with no diagnostics, and each skill loads through load_skill with instructions and files.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Wrote five skills under `skills/`, each grounded in the corpus and written as procedure + verification + pitfalls rather than as citable documentation:

- `granular-synthesis` — Sampling and Enveloping samplers (the segment model and the transposition relation), Arrays 2.9, and the tabread4~/tabplay~/soundfiler/phasor~/cos~/line~/samphold~ pages. Procedure: load into an array, pick the read primitive, schedule (control-rate metro vs signal-rate phasor), window every grain, control pitch, gain-stage, verify with run_patch/analyze_audio.
- `dsp-debugging` — triage order (running? signal/control? reblocking? switching? order of execution? numbers?) from Switching and blocking 2.5.4, the block~/switch~ pages, Subpatches 2.8, the book's order-of-execution examples, and the line~/env~/print~/bang~ pages. Denormals and DC offset are explicitly labelled as not covered by the Pd docs, with the standard practice offered as suggestions.
- `abstractions-and-dollar-zero` — Subpatches 2.8/2.8.1, Dollar signs 2.7.6, graph-on-parent 2.8.2, plus the clone/pdcontrol/value/text pages: creation- vs message-time $ expansion, $0-local names, IEMgui escaping, clone instance numbers, pdcontrol args.
- `working-from-a-pd-file` — the canvas format the project's own pd_file.py writes (#N canvas, #X obj/msg/text/floatatom/connect/restore/array), how to rebuild the key path with write_patch, how to revise session patches by id, and the honest limitation that there is no tool that opens an uploaded file.
- `pd-flavors` — 'Other flavors' (1.4): Vanilla is the documented distribution, forks change GUI and pre-install externals, Pd-extended was discontinued in 2014, active flavors are on puredata.info. Instructions to identify the flavor, check the object reference, and never invent fork-specific menus or objects.

Every SKILL.md omits `version` on purpose: the version is a content digest, so any edit is a new version for TASK-5.15 to judge (declared versions are still supported).

Golden sets: `skills/<name>/golden.json`, 24 questions total (5/5/5/5/4), each with a `notes` field describing what a good answer does and, where a term is robust, an `expects` keyword. No question duplicates evaluation/golden_dataset*.json; the granular set deliberately targets the skill's procedures (windowing, pitch relation, tabplay~ trade-off) rather than repeating the existing granular question. `agent/skills/golden.py` validates the schema (skill name matches the folder, 3–12 unique-id questions, non-empty expects/notes types) and raises SkillError, so a bad slice fails loudly; it is the loader TASK-5.15 will use. `golden.json` is hashed into the skill digest but excluded from the bundled-file list `load_skill` shows the model.

Docs: skills/README.md gained the golden-set schema and the five installed skills; the main README and docs/AGENTIC.md note the initial skills. Effect on the agent surface: with skills installed, `default_config` now includes the skill rules and every request carries the catalogue (~1.4k chars), and 5 skill bodies are available on demand — the generation eval gate will measure that on the next PR touching agent/**. Tests: tests/test_skill_golden.py (21: loader validation + shipped-content checks) and one added digest/listing test in tests/test_skills.py; full suite 769 passed, 17 skipped, 4 deselected.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Wrote the first five Pd skills and a golden set for each.

Skills: granular-synthesis, dsp-debugging, abstractions-and-dollar-zero, working-from-a-pd-file, pd-flavors. Each SKILL.md has a one-line trigger description (shown to the model in the load_skill catalogue) and grounded instructions drawn from the corpus: the Sampling/Enveloping samplers chapters, manual 2.5.4 switching/blocking, 2.7.6 dollar signs, 2.8 subpatches/abstractions, 2.9 arrays, and the IEM object pages. Content is procedure + verification + pitfalls; where the docs do not cover a topic (denormals, fork internals) the skill says so and labels advice as suggestion. Versions are content digests (no declared version), so every edit is a new version for TASK-5.15.

Golden sets: skills/<name>/golden.json, 24 questions across the five skills (5/5/5/5/4), each with notes on what a good answer does and optional expects keywords. agent/skills/golden.py validates the schema (skill/folder match, 3–12 unique-id questions) and raises SkillError; golden.json is part of the version digest but not advertised as a bundled file.

Verified: pytest tests/test_skill_golden.py (21 passed) covers loader validation plus the shipped checks (>=4 skills, one-line trigger descriptions, every skill has a valid golden set, ids unique across skills, load_skill loads each); the registry scans skills/ with zero diagnostics and 5 skills / 24 questions. Full suite: 769 passed, 17 skipped, 4 deselected (-m 'not slow'). Docs updated in skills/README.md, README and docs/AGENTIC.md. Note: with skills installed the agent prompt now carries the skill rules and the load_skill catalogue, so the generation eval gate should be run on the next PR that touches agent/**.
<!-- SECTION:FINAL_SUMMARY:END -->
