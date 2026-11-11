#!/usr/bin/env bash
# Get the hosted demo ready for a clerk's change in the dashboard, in one command (spec 0077):
#
#   scripts/demo/prepare_hosted.sh <clerk email> [vendor ref, default acme]
#
# It reads the team phone from the voice service's DIALBACK_DIAL_ALLOWLIST (never printed, never
# committed), then runs `dialback-core demo prepare` on the core: a fresh demo with the clerk linked,
# the Trust Ledger with that vendor's AR line set to the team phone, then the vendor's change-request
# email. After it, change the vendor's bank details in the dashboard (AP workspace, Demo, Ramp (mock)).
#
# Needs the Railway CLI, logged in, and either a linked project (`railway link`) or
# RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID set.
set -euo pipefail
email="${1:?usage: scripts/demo/prepare_hosted.sh <clerk email> [vendor ref, default acme]}"
ref="${2:-acme}"
where=()
[[ -n "${RAILWAY_PROJECT_ID:-}" ]] && where+=(-p "$RAILWAY_PROJECT_ID")
[[ -n "${RAILWAY_ENVIRONMENT_ID:-}" ]] && where+=(-e "$RAILWAY_ENVIRONMENT_ID")

phone=$(railway ssh "${where[@]}" -s voice -- 'printf %s "$DIALBACK_DIAL_ALLOWLIST"' 2>/dev/null \
  | tr ',' '\n' | tr -d ' \r' | grep -E '^\+[1-9][0-9]{7,14}$' | head -1 || true)
if [[ -z "$phone" ]]; then
  echo "the voice service's DIALBACK_DIAL_ALLOWLIST has no E.164 number (or Railway couldn't be reached); nothing changed" >&2
  exit 1
fi
echo "team phone read from the voice service's allowlist (not shown)"
railway ssh "${where[@]}" -s core -- dialback-core demo prepare --link "clerk=$email" --phone "$ref=$phone" --request "$ref" \
  2>&1 | grep -v '^INFO:'
