---
id: TASK-5.17
title: Add a pdbot CLI
status: Done
assignee:
  - '@trevor'
created_date: '2026-11-23 20:41'
updated_date: '2026-11-26 07:46'
labels:
  - agent
dependencies:
  - TASK-5.1
parent_task_id: TASK-5
priority: medium
type: feature
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A terminal interface over the agent core: ask questions with streamed output, run local `.pd` files in the sandbox, and replay stored runs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `pdbot ask` streams an answer with tool steps shown
- [x] #2 Patches from an answer can be written to disk as `.pd` files
- [x] #3 `pdbot run <file.pd>` and `pdbot replay <run_id>` work once their dependencies exist
- [x] #4 The CLI is installable via `pyproject.toml` and documented in the README
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add agent/cli.py implementing the pdbot CLI (ask, run, replay, runs) with lazy imports so run/replay/help stay fast and key-free. 2. Configure pyproject.toml with setuptools and console script entry point pdbot = agent.cli:main, and install editable into venv. 3. Add comprehensive unit and CLI integration tests in tests/test_cli.py covering streamed ask with tool steps, writing .pd files to disk, pdbot run <file.pd>, pdbot replay <run_id>, and error paths. 4. Document pdbot installation and usage in README.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Implemented agent/cli.py using stdlib argparse with lazy imports inside each subcommand handler so pdbot --help, run, replay, and runs do not load ChromaDB or require API keys.
- Subcommands: pdbot ask (streams TextDelta to stdout, ToolCall/ToolResult/Sources/footer to stderr, writes .pd and rendered .wav artifacts with -o/--out-dir, supports --session, --db, --no-log, -q/--quiet, and stdin '-'), pdbot run <file.pd> (runs local .pd files through sandbox.run_patch + agent.audio.analyze, supports -s/--seconds, -w/--wav, --json), pdbot replay <run_id> (replays stored runs from RunStore with optional -o/--out-dir .pd export and --json), and pdbot runs (lists recent stored runs).
- Packaging: updated pyproject.toml with setuptools build-system, project metadata, and pdbot = agent.cli:main console script; installed editable into venv; added *.egg-info/ to .gitignore; documented pdbot in README.md.
- Validation: tests/test_cli.py (13 passed) covers ask streaming/steps/TextReset/artifact writing/stdin, run audio/JSON/error paths, replay/runs against SQLite RunStore, and installed venv/bin/pdbot --help subprocess execution. Full non-slow suite: 772 passed, 81 skipped, 4 deselected.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the pdbot CLI (agent/cli.py) with ask, run, replay, and runs subcommands, packaged via pyproject.toml (pdbot = agent.cli:main) and documented in README.md. Verified with 13 new tests in tests/test_cli.py, installed console script smoke checks (pdbot --help, pdbot runs), and the full non-slow test suite (772 passed, 81 skipped, 4 deselected).
<!-- SECTION:FINAL_SUMMARY:END -->
