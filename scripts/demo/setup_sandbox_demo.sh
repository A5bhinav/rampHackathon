#!/usr/bin/env bash
# Make the hosted demo on Ramp's sandbox, in one command (spec 0103):
#
#   scripts/demo/setup_sandbox_demo.sh [the current sandbox dataset's id, to retire it]
#
# A fresh demo dataset on the sandbox, its vendors imported, and Asana, Salesforce and DocuSign in
# the Trust Ledger with the team phone as the number the rule picks. The phone is read from the voice
# service's DIALBACK_DIAL_ALLOWLIST (never printed, never committed). Reject any bank change still
# pending in Ramp first: the import counts whatever is there as already known.
#
# Needs the Railway CLI, logged in, with this folder linked (`railway link`).
set -euo pipefail
ASANA=65d5d31e-7ab2-424a-a04c-3c3696c2e629
SALESFORCE=289a447c-11ed-4302-bdb9-6545d81faa0d
DOCUSIGN=002909c0-29e4-40c7-bd04-3a059fe41e7f

phone=$(railway ssh -s voice -- 'printf %s "$DIALBACK_DIAL_ALLOWLIST"' 2>/dev/null \
  | tr ',' '\n' | tr -d ' \r' | grep -E '^\+[1-9][0-9]{7,14}$' | head -1 || true)
if [[ -z "$phone" ]]; then
  echo "the voice service's DIALBACK_DIAL_ALLOWLIST has no E.164 number (or Railway couldn't be reached); nothing changed" >&2
  exit 1
fi
echo "team phone read from the voice service's allowlist (not shown)"

if [[ -n "${1:-}" ]]; then
  railway ssh -s core -- dialback-core retire-dataset --dataset "$1" 2>&1 | grep -v '^INFO:' || true
fi
made=$(railway ssh -s core -- dialback-core new-dataset --kind demo --label Ramp-sandbox-demo --ramp sandbox 2>&1)
dataset=$(printf '%s\n' "$made" | tr -d '\r' | awk '$1 == "dataset" { print $2 }')
if [[ -z "$dataset" ]]; then
  printf '%s\n' "$made" >&2
  exit 1
fi
echo "dataset $dataset"
railway ssh -s core -- dialback-core import-vendors --dataset "$dataset" 2>&1 | grep -v '^INFO:'
railway ssh -s core -- dialback-ledger demo load --dataset "$dataset" --with-requests --like acme \
  --real-vendor "asana=$ASANA=Asana" --real-vendor "salesforce=$SALESFORCE=Salesforce" --real-vendor "docusign=$DOCUSIGN=DocuSign" \
  --phone "asana=$phone" --phone "salesforce=$phone" --phone "docusign=$phone" 2>&1 | grep -v '^INFO:'
echo "ready: open the dashboard, pick Ramp-sandbox-demo, then enter a bank change in Ramp"
