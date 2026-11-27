---
id: TASK-5.18
title: Add a Slack bot interface
status: To Do
assignee: []
created_date: '2026-11-23 20:41'
updated_date: '2026-11-27 21:20'
labels:
  - agent
dependencies:
  - TASK-5.30
parent_task_id: TASK-5
priority: low
type: feature
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A Slack app (Bolt, Socket Mode) over the agent core. One thread is one session and one durable run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Mentioning the bot starts a run; replies in the thread continue the same session
- [ ] #2 Answers stream by editing the bot message
- [ ] #3 Patch diagrams and rendered audio are uploaded to the thread as files
- [ ] #4 Expensive tool use asks for approval with interactive buttons
- [ ] #5 Setup is documented, including the Slack app manifest
<!-- AC:END -->
