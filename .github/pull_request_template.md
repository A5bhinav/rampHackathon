## What and why

<!-- One paragraph. Link the design section or ADR if it applies. -->

## Spec

<!-- specs/NNNN-name.md and which acceptance items this PR completes. -->

## Commands run and their output

```
scripts/db/test.sh
```

## Scorecard change

<!-- Required if agent, ledger, decision, prompt or model-pin code moved: paste the lab-smoke gates and what moved since the last run. Otherwise write "not lab-relevant". -->

## Screenshots

<!-- UI changes only. -->

## Checklist

- [ ] Under about 400 changed lines, not counting generated code and fixtures
- [ ] No direct write to `app.cases`, `app.decisions`, `app.releases` or `app.case_event_receipts`
- [ ] No full bank account numbers, raw transcripts, recordings, secrets or real-looking personal data
- [ ] Contract changes are in their own PR (and, after Nov 2, only add optional fields or endpoints)
- [ ] Reviewed against the spec by the read-only reviewer agent in a fresh session
