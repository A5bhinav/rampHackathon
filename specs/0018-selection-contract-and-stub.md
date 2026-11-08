# 0018: Selection contract and the ledger stub

**Owner:** Inesh  **Slice:** evidence  **Target day:** Wed Nov 4

## Goal

The core's `NumberResolver` and `ChangeContext` ports (`apps/core/src/dialback_core/ports.py`)
call `dialback_ledger.select_number_stub`, `select_number` and `change_context`. None of them
exist yet, so every case goes to a person and three decision facts stay unknown. This spec
writes the selection contract the core asked for (spec 0002, D11), then a stub that returns its
examples under `DIALBACK_RESOLVER=stub`. The real rule (`ledger-select-v1`) comes in a later spec.
Serves R2 (the pick never comes from the change context) and R7 (anything unclear fails closed).

## Contract touched

`packages/contracts/schemas/selection.schema.json` (new), with
`examples/selection.picked.json` and `examples/selection.no-provable-number.json`. This goes
in its own PR, `contract: selection`. The stub is in a second PR.

## Acceptance

Contract PR:

- [ ] The schema carries D11's fields: `number_selection_id`, `rule_version`, and either a
      `contact_point_id` (a pick) or a `reason` (no pick). The names match what
      `selection_from()` in `ports.py` reads, and `source_types` matches `NumberResolvedBody`.
- [ ] `outcome` is `picked` or `no_provable_number`. A pick requires `contact_point_id` and
      `e164`, and allows up to two `alternates`. No pick forbids `contact_point_id`, `e164` and
      `alternates`, and requires `reason`.
- [ ] Every candidate the rule looked at is listed with `eligible` and machine-readable
      `reasons`. A candidate the rule rejected must carry at least one reason code.
- [ ] The replay record is in the contract: `cutoff`, `rule_settings` (integers only) and
      `inputs_sha256`, so a selection can be re-run from the rows it read.
- [ ] Phone numbers are E.164. The examples use only the fictional 555-0100 to 555-0199 range.
      Nothing in the schema can hold a bank account number.
- [ ] Both examples validate (`validate.py`).

Stub PR:

- [ ] `dialback_ledger.select_number_stub(case_id=, dataset_id=, vendor_id=, cutoff=)` returns
      the `picked` example as a dict, with `case_id`, `dataset_id`, `vendor_id` and `cutoff`
      replaced by the arguments.
- [ ] `dialback_ledger.change_context(...)` returns `{}`, so all three facts stay unknown
      (`None`), as they are today. A real answer comes with the selection rule.
- [ ] `dialback_ledger.select_number` does not exist yet, so `DIALBACK_RESOLVER=ledger` still
      fails closed (`no_provable_number`, the case goes to a person).
- [ ] The stub's output validates against the schema.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Contract items | `validate.py` over the two examples |
| Stub returns the example with the arguments | `packages/ledger/tests/test_stub.py::test_stub_returns_the_picked_example` |
| Stub output matches the schema | `packages/ledger/tests/test_stub.py::test_stub_output_matches_the_schema` |
| Change context is unknown | `packages/ledger/tests/test_stub.py::test_change_context_is_unknown` |
| `ledger` mode fails closed | `packages/ledger/tests/test_stub.py::test_no_real_select_number_yet` |
| The core reads the stub | `cd apps/core && uv run pytest`, `uv run python scripts/dev/stack.py --smoke` |

## Out of scope

The `ledger` schema and its tables (next spec). `ledger-select-v1` and its property tests. The
real `change_context`. Persisting the stub's selection: its `number_selection_id` and
`contact_point_id` are fixed example UUIDs that no table holds yet.

## Open questions

- **Q38 (Abhi):** the core passes the change's detection time as `cutoff`. The ledger README
  defines the cutoff as the *earliest* of the request's arrival, the first lookalike-domain
  message and the change appearing in Ramp, which can be earlier. The schema records both:
  `requested_cutoff` (what the core passed) and `cutoff` (what the rule used). Is that enough,
  or should the core pass the request's arrival time too?
- **Q39 (Abhi, Sami):** the voice service reads the number from `ledger.number_selections`, not
  from the core (D11). Should it read `e164` from the stored selection document, or join
  `contact_points`? Either works with this schema.
- **Q18 (open, spec 0006):** may the voice service dial an alternate? This schema lists
  alternates but says nothing about when they're used.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
