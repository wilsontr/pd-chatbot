---
id: TASK-5.13
title: Add a runtime skills registry with load_skill
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-25 23:26'
labels:
  - agent
dependencies:
  - TASK-5.1
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Support Agent Skills (folders with a `SKILL.md` and optional scripts). The agent sees only each skill's name and description and calls `load_skill(name)` to pull in the full instructions when needed. Skills are versioned and the registry is re-read each turn, so a new skill appears mid-session without a redeploy.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The agent sees skill names and descriptions only until it calls `load_skill`
- [x] #2 Adding or updating a skill in the watched location takes effect on the next turn without a restart
- [x] #3 Each run records which skill versions it loaded
- [x] #4 Tests cover discovery, loading and hot reload
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add an `agent/skills/` package that discovers Agent Skills folders (`<root>/<name>/SKILL.md`) from a configured watched directory (`SKILLS_DIR`, default `skills/`). Parse the flat front matter (`name`, `description`, optional `version`), keep the body as the instructions, list bundled files, and derive a content digest so an edit changes the version even when no version is declared. Invalid folders (no description, no body, name/folder mismatch, duplicate) are skipped and reported as diagnostics, not crashes.
2. Add a `load_skill` tool in `agent/tools/skills.py`. Its description carries only the catalogue — skill names plus one-line descriptions (progressive disclosure) — and `run(name)` returns the full instructions, the bundled-file list, and `name`/`version`/`digest` in `ToolOutcome.data`. Unknown or missing names return a recovery message listing what exists.
3. Hot reload without a restart: `ToolRegistry.refresh()` calls an optional `refresh()` on tools; the agent loop calls it at the start of every turn, so a skill added mid-run appears in the next turn's tool schema. `load_skill` re-reads the skill folder from disk on every call, so an edited `SKILL.md` is picked up and gets a new version. The durable path already rebuilds tools per turn.
4. Record loaded skill versions: `LoopState.loaded_skills`, `Done.skills` (event log + `done` frame), `agent/chat.py` trace metadata, `AgentAnswer.loaded_skills`, `RunStore.reconstruct_state`, and the durable workflow's `Done`.
5. Wire `load_skill` into `default_registry` and add the skill rules to `agent_system_prompt` only when skills are installed, so an empty registry does not change the prompt.
6. Document the format in `skills/README.md` and the README/AGENTIC notes, and add tests for discovery, loading, hot reload and version recording.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented. New `agent/skills/` package: discovers `<root>/<name>/SKILL.md` (root = `SKILLS_DIR` or `skills/`), parses the flat `key: value` front matter (`name`, `description`, optional `version`), keeps the body as instructions, lists bundled files and hashes every file into a content digest. Invalid folders (missing description/body, name != folder, duplicate name, bad slug) are skipped with a `SkillDiagnostic` and a warning rather than breaking discovery; a directory without a `SKILL.md` (the `skills/` README) is simply not a skill. A declared `version` wins; otherwise the version is the first 12 hex of the digest, so edits change the version without anyone bumping a number.

`agent/tools/skills.py` adds `load_skill`. Its `spec` is a property, so the catalogue it advertises (name + one-line description only) is computed from the current snapshot each time the model request is assembled. `run(name)` re-reads the skill folder from disk (so an edit or even a mid-turn addition is served), returns the instructions, a truncated-at-60k guard, the bundled-file list and `data.loaded_skill = {name, version, digest}`. Unknown/missing names return a recovery message listing what exists; an empty registry says none are installed and tells the model not to retry.

Hot reload: `ToolRegistry.refresh()` calls the optional `RefreshableTool.refresh()` (new protocol in `agent/tools/__init__.py`, failures logged not raised), and `AgentLoop.stream` calls it at the top of every turn. The durable path already rebuilds its tools from `tools_factory` per DBOS step, so it gets the same behaviour. `default_registry` always registers `load_skill` (the policy allowlist gained `SKILL_TOOLS`) even with an empty watched directory, because otherwise a skill added mid-run could not appear until a new request; with nothing installed the description says so and `default_config` omits the skill rules from the prompt, so an empty registry does not change the measured prompt.

Run record: `LoopState.loaded_skills` (name -> version) is filled from the tool outcome, `Done.skills` carries it in the `done` frame and the run log, `agent/chat.py` adds `skills: name@version` to the trace metadata, `agent/durable.py` threads it into its `Done`, and `RunStore.reconstruct_state` reports it (from both the Done event and the tool results, so old logs work too). `AgentAnswer.loaded_skills` exposes it to evals. No DB schema change: the record rides on existing event payloads.

Docs: `skills/README.md` (format, hot reload, versioning, trust), README "Skills" section and tool diagram, `docs/AGENTIC.md` section 3 marked done. Tests: `tests/test_skills.py` (34 tests) plus one updated assertion in `tests/test_patch_tools.py` for the new tool name.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a runtime Agent Skills registry (`agent/skills/`) plus a `load_skill` tool (`agent/tools/skills.py`), wired through the agent loop, the request layer, the run log and the durable workflow.

What it does: skills live in `$SKILLS_DIR` (default `skills/`) as `<name>/SKILL.md` folders; the model sees only each skill's name and one-line description in the `load_skill` tool description, and the body arrives as the tool result when it calls `load_skill(name)`. Each skill has a version — declared, or a content digest of its files — and every run records the versions it loaded in `Done.skills`, the run log and `RunStore.reconstruct_state`.

Verified with `pytest tests/test_skills.py` (34 tests: discovery/diagnostics, the tool contract, add/edit/delete hot reload, loop-level refresh between turns, and version recording on the streaming and durable run paths) and the full suite: 747 passed, 17 skipped, 4 deselected (`-m 'not slow'`). A scripted serving-path demo against an initially empty watched directory showed turn 1 advertising 'none installed', turn 2 advertising the skill added mid-run, the instructions only in the tool result, and `done.skills` / the replayed run log both carrying `granular-synthesis@134226a0c010`. Docs updated: `skills/README.md`, the README Skills section, and `docs/AGENTIC.md`.
<!-- SECTION:FINAL_SUMMARY:END -->
