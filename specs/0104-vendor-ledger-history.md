# 0104: A vendor's history in the Trust Ledger

**Owner:** Sami  **Slice:** evidence  **Target day:** before the Nov 19 final  **Builds on:** 0023, 0026, 0041

## Goal

The Trust Ledger page (spec 0026) shows one row per vendor: a status, the number on file and the
provable number. The ledger knows much more (every number in every document, where and when each
document arrived, and why the rule refuses each number), but no API or page shows it outside a
case. When this is done, a person clicks a vendor on `/d/[datasetId]/control/trust-ledger` and
sees, as of now, every number the rule considered with the ledger's reason for each, the "first
seen, by source" chart, and the vendor's documents with the exact text each number came from.

## Contract touched

`openapi/dialback.yaml`: `GET /api/datasets/{dataset_id}/vendors/{vendor_id}/ledger`, returning
`VendorLedger` (new schemas `VendorLedger`, `VendorLedgerRule`, `LedgerDocument`,
`PhoneObservation`). It only adds. Its own PR, with this spec.

## Decisions

- **D1. Read only, the risk list's run.** The rule runs as of now with no change request, exactly
  as `risk_list` runs it (one shared function, one `now`), so this page and the vendor's risk row
  always agree. Nothing is stored. The response carries no case id, selection id or inputs hash:
  those name a stored selection, and this isn't one.
- **D2. Phone observations only** (ADR-8). Bank, email and domain observations are left out; a
  bank observation's stored form is routing, last four and a fingerprint, and the page needs none.
- **D3. Document text is redacted on read.** Every run of 7 or more digits in a quote, subject,
  sender or thread id that isn't part of a phone number is replaced with `[digits removed]`, by the
  function the LLM reader already uses (`llm_reader.for_the_model`), so a stored line that carries
  an account number never reaches the page. Known limit: digits split into groups ("0001 2345 6789")
  pass. Text is returned as data and rendered as plain text (R9). Hidden text has no quote.
- **D4. Order and cap.** Only rows recorded before `as_of`, the ones the rule read, so every number
  listed is one the rule judged. Documents newest first by our receive time, each email before its
  attachments (one level; nested attachments aren't ordered); at most 500, with `document_count`
  the total.
- **D5. Words.** Every explanation is the ledger's: `status_text` (risk.py) and `reason_text`
  (reasons.py). Labels and empty states are the page's. "Provable" or "not provable", never "safe".
- **D6. Reuse.** The lane chart and the every-number table move from the case lineage page into a
  shared component; the case page looks the same.

## Acceptance

- [ ] 1. `dialback_ledger.risk.vendor_ledger(conn, dataset_id, vendor_id)` returns `None` when the
      vendor isn't on that dataset, or the caller can't see the dataset (a demo account on a dev one). Otherwise: `risk` equals the vendor's
      `risk_list` row at the same `now` (a vendor with no documents yet reads `missing`); `rule` holds `rule_version`, `rule_settings`, `as_of`,
      `cutoff`, `outcome`, `candidates` (exactly `select()`'s), and `contact_point_id` and
      `alternates` when picked.
- [ ] 2. `documents` lists every ledger document of the vendor recorded before `as_of` (D4), each with
      `document_id`, `kind`, `custody_channel`, `received_at`, `claimed_date`, `sender`, `subject`,
      `thread_id`, `parent_document_id` and its phone observations (`e164`, `role`, `party`,
      `visibility`, `quote`). No bank, email or domain observation appears.
- [ ] 3. A quote or subject holding a 12-digit run comes back with `[digits removed]`, the phone number intact (D3).
- [ ] 4. The endpoint matches the contract; returns 404 for an unknown dataset, an unknown vendor,
      a vendor of another dataset, and a dataset the person can't see; reads under the person's token
      (`person_tx`).
- [ ] 5. Reading writes nothing: no `ledger.contact_points` or `ledger.number_selections` row is added.
- [ ] 6. The page `/d/[datasetId]/control/trust-ledger/[vendorId]` shows the vendor's status badge
      and `status_text`, the lane chart and every-number table with `reason_text` sentences, and the
      documents with their observations and quotes as plain text. Each Trust Ledger row links to it.
      An unknown vendor shows the not-found page.
- [ ] 7. The lineage helpers take the as-of-now run (`split`, `lanePoints`), and the case lineage
      page's tests pass unchanged.

## Tests (written first)

| Item | Test |
|---|---|
| 1, 2, 3, 5 | `packages/ledger/tests/test_risk.py::test_vendor_ledger_*` (real Postgres, as a person) |
| 4 | `apps/core/tests/test_api.py::test_the_vendor_ledger_*` |
| 6, 7 | `apps/web/tests/lineage.test.ts`, `apps/web/tests/vendor-ledger.test.ts` |

The code lands in two PRs stacked on the contract PR, to stay under about 400 lines each: the
ledger function and the endpoint (items 1 to 5), then the page (items 6 and 7).

## Out of scope

The original files or full text (`storage_path` is never written); bank, email and domain
observations; one dot per sighting on the chart (spec 0041, Q4); marking which documents the rule
counts as change context or doesn't count; paging past 500 documents.

## Open questions

- **Q220:** As of now, a vendor with no provable number has no ledger sentence for it: `select()`'s
  `reason` speaks of "the change request", which this run doesn't have. The page shows the status
  sentence and each number's reasons and writes nothing of its own. Should the ledger add one?
- **Q221:** "Now" is the wall clock, as in `risk_list`, not the dataset's clock (`app.now`). On a
  lab dataset whose simulated time runs ahead, rows recorded after the wall clock are left out.
  Keep, or move both to the dataset's clock?
- **Q222:** Should the page mark the documents the rule treats as change context (a lookalike
  sender) or doesn't count (email from outside the vendor's domains)? That needs ledger sentences.
- **Q223:** `reason_text` sentences say "before the request" (for example `too_few_source_types`).
  On this page the rule runs as of now with no request, so the page labels its cutoff "the cutoff"
  while the reasons beside it say "the request". The page shows them as written. Should the ledger
  add as-of-now wordings, or one wording that reads right in both places?

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
cd packages/ledger && uv run pytest tests/test_risk.py -q
cd apps/core && uv run pytest tests/test_api.py -q -k "vendor_risk or vendor_ledger"
python3 scripts/ci/boundaries.py
```
