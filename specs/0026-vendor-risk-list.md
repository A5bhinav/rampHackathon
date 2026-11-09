# 0026: The vendor risk list (demo beat 1)

**Owner:** Inesh (ledger, contract, page), endpoint written for Abhi's review  **Slice:** evidence
**Target day:** Mon Nov 9

## Goal

The demo's opening screen: for each vendor in the ledger, is the phone number AP has on file one
`ledger-select-v1` can prove? A dashboard page at `/d/[datasetId]/risk`, linked from the shell's
nav, lists each vendor with its number on file, the number the rule would pick today, and a status:

| Status | Meaning |
|---|---|
| `provable` | the number on file meets every part of the rule |
| `near_miss` | the number on file is one digit off, or two swapped digits away from, a number the rule can prove |
| `unproven` | there is a number on file, but the rule can't prove it |
| `missing` | the ledger has no number on file for this vendor |

"Number on file" is what the old vendor master says (`vendor_master` sightings, spec 0021). The
rule runs as of now with no change request, so nothing is change context except what arrived
after now.

## Contract touched

`openapi/dialback.yaml`: `GET /api/datasets/{dataset_id}/vendor-risk`, returning `{vendors: [VendorRisk]}`
with `vendor_id`, `legal_name`, `status`, `number_on_file`, `provable_number` and `status_text`
(the ledger's sentence for the status, so the page writes none). It only adds. Its own PR.

## Acceptance

- [ ] `dialback_ledger.risk.risk_list(conn, dataset_id)` reads under the caller's connection (the
      person's token) and returns one row per vendor with ledger documents, ordered worst first
      (near miss, unproven, missing, provable), then by name.
- [ ] Each status is decided by code from the rule's candidates, as in the table.
- [ ] The demo seed gives one of each kind besides `missing`: acme `provable`, brightline
      `unproven`, cobalt `near_miss`; its three selections don't change (on-file records never count).
- [ ] The endpoint returns 404 for a dataset the person can't see, and is checked against the contract.
- [ ] The page lists the vendors with their status as a badge, both numbers and the ledger's
      sentence; it writes no explanation of its own; the shell's nav links it.

## Tests (written first)

| Item | Test |
|---|---|
| ledger | `packages/ledger/tests/test_risk.py` (real Postgres) |
| endpoint | `apps/core/tests/test_api.py::test_the_vendor_risk_list*` |
| page helpers | `apps/web/tests/risk.test.ts` |

## Out of scope

Links from a vendor to its cases, refreshing on a schedule, vendors with no ledger documents.

## Open questions

- **Q52 (Abhi):** like the selection endpoint, `api.py` imports a ledger function (`risk_list`) and
  passes it the person's connection. Fine, or would you rather the ledger expose a SQL view?

## Commands

```bash
cd packages/ledger && uv run pytest -q
```

```bash
cd apps/core && uv run pytest -q
```

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```
