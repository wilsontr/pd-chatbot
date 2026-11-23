---
id: TASK-5.4
title: Build a hardened headless-Pd sandbox
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
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
- [ ] #1 Given a `.pd` file and a duration, the sandbox returns console output, a WAV file and an exit status
- [ ] #2 Runs have no network access, CPU/memory/wall-clock limits, a temporary filesystem, a read-only base image and capped output size
- [ ] #3 Tests show that a patch that loops forever, fills memory or writes large files is stopped cleanly
- [ ] #4 The hardening choices are documented
<!-- AC:END -->
