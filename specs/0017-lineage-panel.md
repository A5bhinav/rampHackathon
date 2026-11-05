# 0017: The number lineage panel

**Owner:** Inesh (contract and page), endpoint written by Inesh for Abhi's review  **Slice:** evidence  **Target day:** Wed Nov 4

## Goal

A dashboard page at `/d/[datasetId]/cases/[caseId]/lineage`. It shows the number the case will
call, where it came from (source types, first seen, how many sightings), the alternates, and
every number the rule refused with the reason in words. A reviewer and a judge can see *why*
this number and not the one in the email (R2), from the selection the case actually used.

Three PRs, because the work crosses three owners:

1. **Contract** (all): `GET /api/cases/{case_id}/selection` in `openapi/dialback.yaml`,
   returning a `SelectionView`, and the regenerated TypeScript.
2. **Core endpoint** (Abhi's `apps/core`, reviewed by Abhi): reads the selection named by the
   case's latest `number_resolved` or `no_provable_number` event under the person's own token,
   with the sentence for each reason code from the ledger's templates (`dialback_ledger.reasons`).
3. **Page** (Inesh): the page, a link from case detail, and pure helpers with tests.

## Contract touched

`openapi/dialback.yaml`: a new path and the `SelectionView` schema (`selection`, which is
`selection.schema.json` or `null`, and `reason_text`, mapping each reason code to its template
sentence, and `notes`, the ledger's sentences for the no-selection, no-pick and alternates
states). It only adds, so it's allowed after Nov 2. Its own PR.

## Acceptance

- [ ] 404 `not_found` for a case the person can't see or that doesn't exist.
- [ ] 200 with `selection: null` when the case has no selection event, or its event names no
      stored selection (the stub's, or "ledger unavailable").
- [ ] Otherwise, the stored selection document, exactly as `ledger.number_selections` holds it.
- [ ] `reason_text` covers every reason code (and only codes) in `selection.schema.json`. Each
      sentence comes from the ledger and says "provable" or "not provable", never "safe".
- [ ] The page lists the pick, the alternates and every refused number with its reasons in words,
      the cutoff and the rule version. All text comes from the API; values are shown as plain text.
- [ ] When there's no selection, the page says so, and why that keeps the hold on.
- [ ] Case detail links to the lineage page.

## Tests (written first)

| Item | Test |
|---|---|
| contract | `validate.py`; `pnpm check:api` |
| endpoint | `apps/core/tests/test_api.py::test_the_selection_*` (checked against the contract) |
| templates | `packages/ledger/tests/test_reasons.py` |
| page helpers | `apps/web/tests/lineage.test.ts` |

## Out of scope

Writing selections (the database wiring, Q45: until then the live stack shows "no selection").
The vendor risk list. A lineage view across cases.

## Open questions

- **Q48 (Abhi):** the endpoint reads `ledger.number_selections` through the person's token (RLS
  lets signed-in users read). Is it fine for `api.py` to import `dialback_ledger.reasons`?
  The core already depends on the ledger package.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```

```bash
cd apps/core && uv run pytest -q
```
