---
id: TASK-5.26
title: Trial Modal as the production host for the Pd sandbox
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24 23:03'
updated_date: '2026-11-24 23:42'
labels:
  - agent
  - infra
dependencies:
  - TASK-5.4
references:
  - sandbox/runner.py
  - sandbox/pdrun.c
  - 'https://modal.com/docs/guide/sandboxes'
  - 'https://modal.com/docs/guide/sandbox-networking'
  - 'https://modal.com/docs/guide/existing-images'
documentation:
  - docs/SANDBOX.md
parent_task_id: TASK-5
priority: high
type: spike
ordinal: 35000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Pd sandbox from TASK-5.4 needs Docker, which an ordinary Railway service does not provide, so patch execution cannot ship to production yet. A documentation-only comparison on 2026-11-24 favoured Modal: Python SDK, all networking can be blocked, gVisor or VM isolation, hard CPU/memory limits, and $30 of free credit a month. Find out by running it whether Modal can host the sandbox without losing the hardening in docs/SANDBOX.md, and decide where to host.

Known concerns: Modal ignores `USER` and runs containers as root; read-only root, tmpfs and pids limits are not documented; stdin together with binary output on a sandbox main process is unconfirmed; images must be linux/amd64, which the sandbox has not been tested on.

Others considered: E2B (rejects distroless and multi-stage Dockerfiles), Cloud Run sandboxes (preview), Fly Machines (private-network blocking unclear), Railway Sandboxes (outbound internet cannot be disabled, beta). Fallback: a small VM running Docker with gVisor. Needs a Modal account and token supplied by the project owner.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The sandbox renders a patch on Modal from stdin and returns console, WAV and exit status through the same `SandboxResult` contract
- [x] #2 Each hardening choice in docs/SANDBOX.md is recorded as kept, replaced or lost on Modal, with evidence from inside a running sandbox
- [x] #3 The runaway-patch tests (endless loop, memory, large files, console flood) run against Modal, and their status labels are recorded
- [x] #4 Cold and warm start latency and cost per run are measured over at least 20 runs each
- [x] #5 A go/no-go decision is documented in docs/SANDBOX.md; a no-go names the next option to pursue
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe Modal (done): main-process stdin works, stdout is UTF-8 only so the frame must be base64; gVisor runtime refuses mounts, VM runtime gives the guest full caps.
2. pdrun: when started as root, mount the tmpfs, remount / read-only, set rlimits, drop caps and uid itself, failing closed; add a seccomp denylist for the patch process; optional base64 output.
3. sandbox/modal_backend.py selected by PD_SANDBOX_BACKEND=modal, same SandboxResult parsing.
4. Run the existing container tests on both backends, with in-sandbox evidence for each limit.
5. Benchmark 20+ cold and 20+ warm runs; compute cost from billing docs.
6. Document per-choice outcome and go/no-go in docs/SANDBOX.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Findings: gVisor runtime refuses the mounts (pdrun fails closed, exit 70); VM runtime allows all of them. Main-process stdout is text only, kept in logs and rate-limited, so pdrun runs via exec with text=False.
Validation: Modal 60 passed, 1 skipped (Docker-only test); Docker 61 passed; 40/40 runaway runs labelled correctly. Cost from modal billing report: 60 runs = USD 0.00124.
Not verified: calling from Railway, gVisor under Docker, worker hosts that have never held the image. Docker-on-x86_64 is still untested, but Modal's workers are x86_64. New runtime dependency: modal. Temporary Modal apps pd-sandbox-trial and pd-sandbox-cost stopped; pd-sandbox kept.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Go: Modal will host the Pd sandbox in production. Added sandbox/modal_backend.py (PD_SANDBOX_BACKEND=modal, VM runtime, all networking blocked) with the same SandboxResult; pdrun now hardens itself when started as root and adds a seccomp denylist on every backend. Verified with the container tests on both backends, from inside the sandbox, plus 40 runaway runs. Warm p50 1.18 s / p95 1.33 s, cold p50 2.56 s / p95 3.14 s, about USD 0.00002 per run. Details and per-choice outcomes in docs/SANDBOX.md.
<!-- SECTION:FINAL_SUMMARY:END -->
