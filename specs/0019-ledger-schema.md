# 0019: The Trust Ledger schema

**Owner:** Inesh  **Slice:** evidence  **Target day:** Wed Nov 4

## Goal

`supabase/migrations/ledger/0001_ledger.sql` creates the four tables the ledger README
names: `documents`, `observations`, `contact_points`, `number_selections`. The database itself
enforces the rules the selection rule depends on, so a bug in the Python can't break them:
provenance comes from custody, never from a model (ADR-13, R9); sightings are append-only and
replayable; no full bank account number is ever stored (ADR-8); and a stored selection agrees
with its own document (spec 0018).

## Contract touched

None. `number_selections.selection` stores a `selection.schema.json` document (spec 0018); the
table checks only the fields it also keeps as columns.

## Acceptance

- [ ] Every table has `dataset_id`. `vendor_id` references `app.vendors`; `case_id` references `app.cases`.
- [ ] A document records custody: channel, our own `received_at`, `claimed_date` (shown only),
      sender, thread, subject, SHA-256. An attachment must have its parent email's channel and
      receive time.
- [ ] An observation's `source_type` and `seen_at` are copied from its document by a trigger.
      Whatever the insert says is overwritten, so a model's output can never set them.
- [ ] `observations`, `number_selections` and `documents` are append-only: update, delete and
      truncate are refused by grants and by a trigger, even for the table owner.
- [ ] `observations.recorded_at` is the database's clock (`app.now(dataset_id)`), so a
      selection can read "rows recorded before the case opened".
- [ ] A phone observation must be E.164. A bank-account observation must be
      `routing:last4:fingerprint` and must have no quote, so no full number can be stored.
- [ ] A selection row's `outcome`, `case_id`, `rule_version`, `contact_point_id` and
      `inputs_sha256` must match its stored document. A pick must name a contact point; no
      pick must not.
- [ ] `contact_points` is a summary the service role may update, but only its summary columns:
      its dataset, vendor and number are fixed once written (grant and trigger). One row per (vendor, number).
- [ ] A selection is for its case's dataset and vendor, and the contact point it names is that
      vendor's, with the number the document names.
- [ ] Append-only holds for truncate too. It holds while the triggers stay in place; the owner or
      a superuser could still disable them, as for `audit.evidence_events`.
- [ ] Signed-in users read everything and write nothing; the service role inserts; anon has no access.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| all of the above | `supabase/tests/011_ledger.sql` (one block per item) |
| nothing else breaks | `scripts/db/test.sh`, `cd apps/core && uv run pytest` |

## Out of scope

Ingestion code, entity resolution (a document with no `vendor_id` yet is allowed; confirming a
fuzzy match is a later spec), the selection rule, a `lineage` view, seeds.

## Open questions

- **Q40 (Abhi):** a demo reset starts a new dataset. Should the ledger's documents be copied
  into the new dataset, or should `ledger` rows be readable across datasets for the same
  company? This schema keeps them per dataset, like `app`. (Relates to Q37.)
- **Q41 (Sami):** the voice service reads the number to dial. This schema offers both
  `number_selections.selection ->> 'e164'` and a join to `contact_points.e164` (Q39).

## Commands

```bash
scripts/db/test.sh
```
