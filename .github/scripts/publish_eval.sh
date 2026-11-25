#!/usr/bin/env bash
# Commit docs/EVAL.md to main, even if main moved while the evaluation ran.
#
# If only Backlog or Markdown files changed since the measured commit, the
# results still describe main and are committed on its tip. Otherwise they are
# not published: the newer commit's own run will do it.
set -euo pipefail

report=docs/EVAL.md
measured=$(git rev-parse HEAD)

if git diff --quiet -- "$report"; then
  echo "No change to $report"
  exit 0
fi

git config user.name 'github-actions[bot]'
git config user.email 'github-actions[bot]@users.noreply.github.com'
saved=$(mktemp)
cp "$report" "$saved"

for attempt in 1 2 3; do
  git fetch -q --depth=1 origin main
  tip=$(git rev-parse FETCH_HEAD)
  changed=$(git diff --name-only "$measured" "$tip" -- . ':(exclude)backlog' ':(exclude,glob)**/*.md')
  if [[ -n "$changed" ]]; then
    echo "::notice::main moved to ${tip:0:7} with code changes; not publishing results for ${measured:0:7}"
    exit 0
  fi
  git checkout -q --force --detach "$tip"
  cp "$saved" "$report"
  if git diff --quiet -- "$report"; then
    echo "main already has these results"
    exit 0
  fi
  git commit -q -m 'ci: update evaluation results [skip ci]' -- "$report"
  if git push -q origin HEAD:refs/heads/main; then
    echo "Published on ${tip:0:7}"
    exit 0
  fi
  echo "Push rejected (attempt $attempt)"
done

echo "::error::could not push $report to main"
exit 1
