#!/usr/bin/env bash
# Vercel's Ignored Build Step for the dashboard ("ignoreCommand" in apps/web/vercel.json).
# Vercel runs it from the Root Directory (apps/web) before each build:
#   exit 0: skip the build (the deployment ends as Canceled)
#   exit 1: build
#
# It skips only when it can show that nothing the dashboard build reads changed between the
# commit of this branch's last successful deployment (VERCEL_GIT_PREVIOUS_SHA) and this one.
# Anything it can't show builds: a branch's first deployment, a previous commit missing from
# Vercel's shallow clone, a git error, or a redeploy of the same commit.
#
# A skipped build still counts as a deployment toward Vercel's daily limit (docs/deploy.md);
# skipping saves the build time and frees the build slot.

set -u

build() { echo "vercel-ignore-build: building: $1"; exit 1; }
skip() { echo "vercel-ignore-build: skipping: $1"; exit 0; }

# Everything the dashboard build reads, from the repository root: its own folder, the
# generated API types and OpenAPI file it checks, and the pnpm workspace files the install uses.
paths=(
  apps/web
  packages/contracts
  package.json
  pnpm-lock.yaml
  pnpm-workspace.yaml
  .npmrc
)

previous="${VERCEL_GIT_PREVIOUS_SHA:-}"
[ -n "$previous" ] || build "no earlier successful deployment of this branch to compare with"

top="$(git rev-parse --show-toplevel 2>/dev/null)" || build "not in a git checkout"
cd "$top" || build "can't enter the repository root"

head="$(git rev-parse --verify --quiet "HEAD^{commit}")" || build "can't read HEAD"
base="$(git rev-parse --verify --quiet "$previous^{commit}")" ||
  build "the last deployed commit $previous isn't in this clone"
[ "$base" != "$head" ] || build "same commit as the last deployment (a redeploy)"

# A two-dot diff compares the two trees directly, so it needs no history between them.
changed="$(git diff --name-only "$base" "$head" -- "${paths[@]}")" || build "git diff failed"
[ -z "$changed" ] || build "dashboard files changed since $base: $(echo "$changed" | head -5 | tr '\n' ' ')"
skip "nothing the dashboard reads changed since $base"
