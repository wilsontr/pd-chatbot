---
id: TASK-6
title: Migrate Railway deploy config from railway.json to IaC
status: Done
assignee: []
created_date: '2026-11-23 20:50'
updated_date: '2026-11-25 01:45'
labels:
  - infra
dependencies: []
priority: high
ordinal: 25000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Railway CLI 5.75 warns Config as Code (railway.json) stops being read on 2026-12-01. Move the pd-chatbot service's build/deploy settings to Infrastructure as Code (.railway/railway.ts) so deploys keep the same build command, start command, healthcheck and restart policy. Target: project 'pd chatbot', environment production, service pd-chatbot.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 IaC authoring file is committed on a branch
- [x] #2 Build command, start command, healthcheck path /health and restart policy ON_FAILURE carry over unchanged
- [x] #3 railway config plan shows no unintended changes
- [x] #4 railway.json removed and service's Railway Config File setting cleared, after user confirms
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Branch claude/railway-iac-migration (commit 147e4f7) adds .railway/railway.ts (from `railway config migrate --variables unmanaged`) plus .railway/package.json for the railway SDK.
- migrate dropped restartPolicyType; added deploy.restartPolicyType ON_FAILURE explicitly. The desired graph includes it; the plan shows no diff because ON_FAILURE is Railway's default.
- Plan: 0 add, 2 change, 0 destroy. It sets builder NIXPACKS, buildCommand, startCommand, and healthcheckPath /health on the service (they are null in the dashboard today; railway.json supplied them at deploy time). No other changes.
- Dashboard currently says builder RAILPACK by default, so railway.json must not be removed before apply. Otherwise the next deploy would build with Railpack and no commands.
- `config migrate status` reports no service has a custom Config File path, so cutover has nothing to clear. railway.json is read from the repo root by default.
Remaining (needs user go-ahead): railway config apply, then delete railway.json and merge to main.

Applied 2026-11-23 via `railway config apply --plan` (the same 0/2/0 plan). Read-back shows the build/start/healthcheck settings on the service, and a re-plan reports no drift. The redeploy 09677a0f finished SUCCESS and /health returns 200. railway.json was removed in commit cbcda3d on claude/railway-iac-migration. Not yet pushed or merged: until it reaches main, deploys from main still read railway.json, which has the same values.

Merged to main as 083d41f and deployed from 561887c (75d23a52, SUCCESS): manifest has NIXPACKS, build and start commands, /health and ON_FAILURE with no railway.json; /health 200; re-plan reports no drift.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Railway build and deploy settings now come from .railway/railway.ts; railway.json is gone. Verified by the deployed manifest, /health and a clean config plan after the merge.
<!-- SECTION:FINAL_SUMMARY:END -->
