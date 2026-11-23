---
id: TASK-5
title: Agentic pd-chatbot
status: To Do
assignee: []
created_date: '2026-11-23 20:40'
labels:
  - agent
dependencies: []
documentation:
  - docs/AGENTIC.md
priority: high
type: feature
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Turn pd-chatbot from a fixed RAG pipeline (classify, retrieve, generate, validate) into a tool-using agent that can run and check its own Pd patches. The aim is a portfolio piece that shows real agentic engineering: tools whose results change the outcome, durable runs, skills loaded at runtime, several interfaces on one core, and evals proving the agent beats the pipeline.

Roadmap and rationale: `docs/AGENTIC.md`. Work is split into the subtasks of this task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All subtasks are Done or explicitly dropped with a recorded reason
- [ ] #2 The published eval report compares agent vs. pipeline on the shared golden set
<!-- AC:END -->
