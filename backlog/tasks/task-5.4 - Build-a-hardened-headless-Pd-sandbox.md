---
id: TASK-5.4
title: Build a hardened headless-Pd sandbox
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23 20:40'
updated_date: '2026-11-24 22:52'
labels:
  - agent
dependencies: []
documentation:
  - docs/AGENTIC.md
parent_task_id: TASK-5
priority: high
type: feature
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A container that runs an untrusted `.pd` file in Pd with no GUI for a set number of seconds, renders audio to a WAV file, and returns Pd's console output (e.g. "couldn't create" errors). This is what lets the agent verify its patches by running them. Hosting options (self-hosted Docker plus gVisor, E2B, Modal, Fly Machines) are discussed in `docs/AGENTIC.md`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Given a `.pd` file and a duration, the sandbox returns console output, a WAV file and an exit status
- [x] #2 Runs have no network access, CPU/memory/wall-clock limits, a temporary filesystem, a read-only base image and capped output size
- [x] #3 Tests show that a patch that loops forever, fills memory or writes large files is stopped cleanly
- [x] #4 The hardening choices are documented
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Self-hosted Docker, optional gVisor runtime via env (the option docs/AGENTIC.md calls simplest). Hosting in production (Railway cannot launch containers) stays out of scope.
2. sandbox/pdrun.c: in-container supervisor that forks a child embedding Pd through libpd, renders offline from dac~ into shared memory, enforces the wall clock and console cap, and emits a framed result (JSON header, console, WAV) on stdout. No host mounts: patch in on stdin.
3. sandbox/Dockerfile: multi-stage, pinned libpd and base; runtime stage holds only the binary and its libs, non-root.
4. sandbox/runner.py: run_patch(pd_text, seconds) -> SandboxResult, with all docker limits, bounded reads, host-side deadline, guaranteed cleanup; CLI python -m sandbox.
5. tests/test_sandbox.py: unit tests without Docker, plus container tests for good/broken patches, infinite loop, memory, large files, console flood, network and read-only fs. CI workflow runs them.
6. docs/SANDBOX.md for hardening choices.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Chose libpd (Pd 0.56) over stock pd -batch because stock Pd cannot record [dac~] without a sound card or editing the patch. Statuses, limits and known gaps are in docs/SANDBOX.md.
Validation: tests/test_sandbox.py 59 passed against the real container on Docker Desktop arm64 (runc); full suite 547 passed, 17 skipped, 4 slow deselected. Manually SIGKILLed the host process mid-run and forced a host-side deadline: no container left in either case.
Not verified: gVisor (PD_SANDBOX_RUNTIME=runsc), x86_64, and the new CI workflow (runs on push). Railway cannot start containers, so production hosting is still undecided.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added sandbox/ (Dockerfile, pdrun.c supervisor, runner.py, CLI): run_patch(pd_text, seconds) returns console, WAV, status and exit code from a fresh container with no network, read-only root, 32 MB tmpfs, CPU/memory/pids/wall/file-size limits and output capped in the container and on the host. Tests run endless loops, memory hogs, large files, console floods, thread bombs and quits against the real container, and read the applied limits from inside it (59 passed). Hardening documented in docs/SANDBOX.md; .github/workflows/sandbox.yml builds the image and requires the tests.
<!-- SECTION:FINAL_SUMMARY:END -->
