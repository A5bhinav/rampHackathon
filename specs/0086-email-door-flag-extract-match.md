# 0086: Email door, flag, extract and match (PR 3 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

Read an email that reached the accounts-payable inbox and decide whether it asks to change where a vendor's payments go (a bank-change request), pull out what the email says about the new destination without keeping a full account number (ADR-8), and match the email to a vendor. This PR is built in three tasks: Task 5 writes the labeled test emails (this stub covers it), Task 6 writes flagging, extraction and redaction, Task 7 writes vendor matching. Emails are data, never instructions (R9).

## Contract touched

None.

## Acceptance

- [ ] Task 5: 92 tuning and 100 held-out labeled emails exist, one JSON file each (`subject`, `sender`, `text`, `label`, `vendor`, `note`), and the held-out half is sealed by a SHA-256.
- [ ] Flagging, extraction and redaction: to be completed by Task 6.
- [ ] Vendor matching: to be completed by Task 7.

## Tests (written first)

Fixtures live in `apps/core/tests/email_door/fixtures/`.

- `tuning/`: 30 `change_request` and 62 `ordinary` emails (`cr-NN.json`, `ord-NN.json`). Three of the change requests are the `vendors.*.request` emails of `packages/ledger/src/dialback_ledger/demo_seed.json`, span text joined by newlines. The flagging code may be tuned against these.
- `heldout/`: 35 `change_request` and 65 `ordinary` emails, written to the same brief with no sentence shared with the tuning half. Five of the change requests (cr-31..35) use scenarios absent from the tuning half, and several ordinary emails carry full routing and account digits. Not to be read or run against until the flagging code is final.
- `heldout.sha256` holds the output of `cat $(ls heldout/*.json | sort) | shasum -a 256`, so any later edit to the held-out half is visible:

```
6235aebe8431bfa4a907b6022e85af011c596480bc2885ec646f86ef39242edb  -
```

| Acceptance item | Test |
|---|---|
| 1 | to be completed by Task 6/7 (a test that recomputes the hash and checks the file counts) |
| 2 | to be completed by Task 6 |
| 3 | to be completed by Task 7 |

## Out of scope

Opening cases from flagged emails, replying to the sender, and any change to the receiving route (spec 0085).

## Open questions

- Whether a new-vendor onboarding email that carries first-time bank details counts as a change request. The fixtures avoid this case.

## Commands

```bash
cat $(ls apps/core/tests/email_door/fixtures/heldout/*.json | sort) | shasum -a 256
```
