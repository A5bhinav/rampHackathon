# 0024: The ledger in the database: ingestion writes, select_number, change_context

**Owner:** Inesh  **Partner:** Abhi  **Slice:** evidence  **Target day:** Sun Nov 8

## Goal

Connect the Trust Ledger to Postgres, so `DIALBACK_RESOLVER=ledger` makes a real, stored
selection the voice service and the lineage panel can read. Today the rule (spec 0021) and
ingestion (spec 0022) are pure functions, and nothing writes their results, so a live case either
gets the stub's example number or no number at all.

- `dialback_ledger.store.write_ingest(...)` writes an `ingest_all()` result to
  `ledger.documents` and `ledger.observations` for one vendor.
- `dialback_ledger.select_number(case_id=, dataset_id=, vendor_id=, cutoff=)`, the name the core's
  `LedgerResolver` already calls, reads the vendor's ledger rows, runs `select()`, stores the
  candidates in `ledger.contact_points` and the document in `ledger.number_selections`, and returns
  what spec 0002 D11 asks for.
- `dialback_ledger.change_context(...)` answers `offered_number_from_request` from the ledger.

Serves R2 (the stored pick is the rule's), R6 (selections are append-only and replayable) and R7
(any error is a refusal, never a pick, and never an exception into the core).

## Decisions (for Abhi to confirm)

1. **Q45, the connection:** the ledger opens its own short-lived connection from the core's
   `DATABASE_URL` and acts as `service_role`, exactly as the core's `system_tx` does. The core's
   `NumberResolver` and `ChangeContext` ports stay unchanged. Without `DATABASE_URL`, both fail
   closed.
2. **Q46, which document is the change request:** a new append-only table,
   `ledger.change_requests (dataset_id, vendor_id, document_id)`, filled by whoever ingests the
   request email (the demo loader, spec 0025, and later email detection). `select_number` uses the
   vendor's latest request recorded before the case opened. With none, the change context is
   unknown and the selection is `no_provable_number` (R7).
3. **The vendor's own domains** (email counts only from them, spec 0021) live in a new
   append-only table, `ledger.vendor_domains`. With none, no email counts.
4. **Ids:** a contact point's id is the selection document's (UUIDv5 of vendor and number), so
   the stored rows and the document agree; a repeated selection with the same inputs stores
   nothing new.
5. **A stored document's id is per dataset** (UUIDv5 of the dataset and the source id). The same
   file can be loaded into several datasets, as every demo reset does, and loading twice into one
   dataset adds nothing. `mark_change_request` takes the source id and maps it the same way.

## Contract touched

None. `ledger/0002_wiring.sql` adds two tables.

## Acceptance

- [ ] `write_ingest` stores documents (an attachment after its email) and observations; custody
      is the database's (spec 0019 triggers).
- [ ] `select_number` on a vendor whose evidence proves a number returns `number_selection_id`,
      `rule_version`, `contact_point_id` and `source_types`; the selection and every candidate are
      stored, and the stored document validates against `selection.schema.json`.
- [ ] Running it twice for the same case and inputs returns the same ids and stores nothing new.
- [ ] The same documents load into two datasets, and twice into one, without error or duplicates.
- [ ] Rows the ledger recorded after the case opened are ignored (replay).
- [ ] No change request, no `DATABASE_URL`, an unreachable database, or any error: a
      no-pick answer with a reason, never an exception, never a stored pick.
- [ ] **Gate:** all 20 frozen corpus scenarios, ingested through `ingest_all` and `write_ingest`
      and selected through the database, reproduce their expected pick and outcome.
- [ ] `change_context` returns `offered_number_from_request` True for a number first seen in the
      change context, False for one seen first outside it, and leaves it out (unknown) for a number
      the ledger never saw. The two speaker facts stay unknown: the ledger stores no names.

## Tests (written first)

| Item | Test |
|---|---|
| all of the above | `packages/ledger/tests/test_store.py` (real Postgres via `scripts/db/pgtemp.py`) |
| tables, grants, append-only | `supabase/tests/012_ledger_wiring.sql` |

## Out of scope

The demo loader (spec 0025), the risk list (spec 0026), the voice service's store (Sami), email
detection, entity resolution.

## Open questions

- **Q45, Q46 (Abhi):** confirm decisions 1 and 2, or say what you'd prefer.
- **Q51 (Abhi):** `DIALBACK_RESOLVER` should switch to `ledger` on the deployed core once the demo
  data is loaded (spec 0025). Until then, `stub` keeps today's behavior.

## Commands

```bash
cd packages/ledger && uv run pytest -q
```

```bash
scripts/db/test.sh
```
