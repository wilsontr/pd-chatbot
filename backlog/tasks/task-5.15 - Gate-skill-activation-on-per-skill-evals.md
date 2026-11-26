---
id: TASK-5.15
title: Gate skill activation on per-skill evals
status: Done
assignee: []
created_date: '2026-11-23 20:41'
updated_date: '2026-11-26 00:22'
labels:
  - agent
dependencies:
  - TASK-5.13
  - TASK-5.7
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Treat a skill as a deployable unit with a quality gate: a skill version is only activated if its own eval slice passes in CI.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CI runs each changed skill's eval slice
- [x] #2 A skill version that fails its slice is not activated, and the previous version stays live
- [x] #3 The eval report shows per-skill results
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented per-skill evaluation, gating and activation.

Serving: the manifest is the deploy artifact. `skills/activation.json` pins one entry per skill — display `version`, content `digest`, who/when activated, and the measured metrics. `skills/.activated/<name>/<digest>/` keeps a snapshot of each activated digest. `SkillRegistry` (`agent/skills/__init__.py`) now resolves every working folder against the manifest: digest matches → serve the working folder; digest differs → serve the snapshot and ignore the ungated edit; no entry, no snapshot, or a snapshot that does not hash to the pinned digest → do not serve (diagnostic). A malformed manifest fails closed. A directory without a manifest is the permissive development view (all temp-dir tests keep working), and `SKILLS_ACTIVATION=off` gives that view in a gated checkout. Activation changes are picked up on the next turn because the loop still refreshes per turn. Pin identity is the digest, not the display version, so a reused `version: 1.0` string cannot smuggle an ungated edit through.

Harness: `evaluation/skills.py`. `run_skill_eval` answers a skill's golden questions with the shipped agent and only that skill installed (isolation), judges with the generation rubric plus `evaluation.claims`, and records loaded-skill rate, task success, tool calls, cost and latency. Thresholds in `evaluation/gates.py`: generation judge floors (faithfulness 4.0, relevance 4.5, citation 4.0) plus `min_load_rate` 0.5 — a skill the model never selects from its description is not improving anything. `check_skill_thresholds` reports each miss by name. CLI: `--changed-from/--skills/--all`, `--gate`, `--activate` (only passing versions), `--report docs/SKILLS.md`, `--render-report`, `--output` (per-question JSON), `--summary`, and `--baseline` for bootstrapping (no measurement, recorded as `metrics.baseline`; CI never calls it).

CI: `.github/workflows/skills.yml` runs on PRs touching `skills/**` (or the registry/harness), computes the changed skill folders from the diff, runs their slices, gates them, activates the winners, and commits `skills/activation.json`, `skills/.activated` and `docs/SKILLS.md` back to the PR branch (`[skip ci]`). A failed slice fails the job and commits nothing, so the previous snapshot keeps serving. Fork PRs cannot push with GITHUB_TOKEN; that fails loudly rather than silently skipping activation. Workflow YAML validated with PyYAML; changed-skill detection exercised against HEAD~1 (returned the five skills).

Report: `python -m evaluation.skills --render-report docs/SKILLS.md` writes the per-skill table (working version, activated version, gate verdict, faithfulness/relevance/citation, task success, load rate). `evaluation/gates.py::skills_summary_markdown` adds a per-skill section to the generated `docs/EVAL.md` (via `evaluate.py::_build_report`), so the main eval report carries it from its next run; a test asserts the section renders. `docs/SKILLS.md` is generated now from the baseline manifest.

Baseline: because introducing the gate cannot retroactively measure the five existing skills without a paid run, `--baseline` pinned their current digests and snapshots with `metrics.baseline = true`, so the report says "baseline (not measured)" rather than inventing numbers. The first measured per-skill results arrive when CI runs on a skill change, or from the paid command `python -m evaluation.skills --all --gate` (roughly a few cents per skill). No paid eval was run while implementing this.

Tests: `tests/test_skill_activation.py` (37) covers manifest read/write and failure modes, snapshot path safety, strict serving, the previous-version-stays-live case, missing/corrupt snapshots, `SKILLS_ACTIVATION=off`, only-filtering, activation taking effect on the next scan, baseline/activate writers, prune, the gate thresholds, `run_skill_eval` with stubbed answer/judge functions (including a failed run and an empty retrieval), the CLI activation/no-activation paths, and the report. `tests/test_skill_golden.py` now reads working content with `activation=False` (a PR edit is legitimately ahead of activation) and separately checks every activated digest has a matching snapshot. Full suite: 807 passed, 17 skipped, 4 deselected (`-m 'not slow'`).

Report detail: to make AC #3 visible now rather than only after the next generation-eval run, `docs/EVAL.md` had the generated "Skills (eval-gated)" section spliced in at the same position `evaluate.py::_build_report` emits it (the helper that produced the text, not a hand-written table). The next CI eval will regenerate the whole file with the same section, so there is no divergence to maintain. The baseline rows show their activated digests and no judge numbers, with `Evidence = baseline` — the honest state until the first measured run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a per-skill eval gate that decides which skill versions may serve.

Serving: `skills/activation.json` pins each skill's content digest (with the activation evidence and metrics), and `skills/.activated/<name>/<digest>/` holds the snapshot. SkillRegistry serves the working folder when its digest matches the manifest, falls back to the snapshot otherwise (so an ungated edit is ignored and the previous version stays live), and serves nothing when there is no entry/snapshot or a snapshot does not match — a corrupt manifest fails closed. A directory without a manifest remains the permissive development view; `SKILLS_ACTIVATION=off` gives that view in a gated checkout. The loop still refreshes per turn, so a newly activated version goes live on the next turn.

Harness: `evaluation/skills.py` runs a skill's golden slice through the shipped agent with only that skill installed, judges it with the generation rubric plus deterministic claim checks, and gates it on the generation judge floors plus a 50% load-rate floor. `--activate` pins only passing versions; `--baseline` bootstrapped the five existing skills unmeasured. `.github/workflows/skills.yml` runs the slices for the skill folders a PR changed, activates the winners, and commits the manifest, snapshots and docs/SKILLS.md back to the branch; a failure commits nothing.

Reports: `docs/SKILLS.md` (per skill: version, activated version, gate, faithfulness, relevance, citation, task success, load rate) and the generated `docs/EVAL.md` "Skills (eval-gated)" section (written by skills_summary_markdown, now present with the baseline rows).

Verified: tests/test_skill_activation.py (37 tests) covers manifest validation, strict serving, previous-version-stays-live, missing/corrupt snapshot, SKILLS_ACTIVATION=off, only-filtering, activation writers, pruning, gate thresholds, the harness with stubbed answer/judge functions, and the CLI activation paths; tests/test_skill_golden.py checks every activated digest has a matching snapshot and reads working content independently of activation. Full suite: 807 passed, 17 skipped, 4 deselected. The workflow YAML parses; changed-skill detection returns the five skills against HEAD~1. No paid eval was run: the five baseline skills are marked unmeasured, and the first measured per-skill results come from CI or `python -m evaluation.skills --all --gate`.
<!-- SECTION:FINAL_SUMMARY:END -->
