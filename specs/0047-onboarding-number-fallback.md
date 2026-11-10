# 0047: The number on file from onboarding, as the selection rule's last resort

**Owner:** Sami  **Slice:** money path (selection) and evidence  **Target day:** Nov 10, 2026. Sami set
this on Nov 9, overriding the freeze dates in AGENTS.md.
**Builds on:** 0021 (the selection rule), 0002 (Ramp sync, first sight). **Replaces, for now:** the
shared-profile route of spec 0045 (PR #93), which stays on the roadmap.
**Needs sign-off from:** Inesh (ledger: the selection rule, the document kind, the demo seed), Abhi
(core: the contacts read at first sight; the mock and its seed), and every contract owner.

## Goal

Today a number is callable only if this customer's own documents show it in two kinds of source, at
least 90 days before the change (spec 0021). The vendor's number on file is shown, never counted. So
a customer with little history for a vendor gets no call, and a person handles the change.

When this spec is done, the phone numbers a vendor record had **when Dialback first saw it** are
kept as a frozen onboarding record. If no number passes today's rule, and that record is at least
90 days older than the change, the case calls the onboarding number. Everything else stays as
today:

- the record is per customer and never shared;
- later contact edits never count;
- today's rule always wins when it can pick a number.

Rules served:

- **R2:** the onboarding record predates the change by 90 days, and a number first seen in the change
  context is still refused.
- **R3:** code picks.
- **R7:** with no change request recorded, or a record that is too young or unclear, nothing is
  picked and a person decides, as today.
- R1, R4, R5, R6, R8 and R9 are unchanged.

## Assumptions and known limits

- **A1. The onboarding number is assumed honest.** Dialback does not verify it against outside
  sources (registry, website). Ramp's onboarding checks the bank account and the tax ID, not the
  phone. An onboarding check that compares the typed number with an independent one is on the
  roadmap (specs 0041 and 0042).
- **A2. The long con.** A fraudster already inside the vendor's mailbox when a customer first sets
  the vendor up can supply their own phone number, wait 90 days, then send a bank change. If this
  customer's documents prove no number, the call reaches the fraudster. Today that case gets no call.
  The guards:
  - 90 days of waiting;
  - today's rule wins whenever this customer's own documents prove a number;
  - D5's same-person check.
- **A3. An insider at onboarding.** A clerk who sets up a vendor can type an accomplice's number as
  its contact. D5 stops the same clerk making the later bank change through this route. Two clerks
  working together are not stopped. Today, an insider must plant a number in two kinds of source
  over 90 days (FINDINGS K01).

## Decisions

- **D1. The onboarding record.**
  - The first time the core syncs a vendor record that it has never seen in this dataset, after the
    dataset's first import, it reads the record's Ramp contacts once. It uses a new
    `RampPort.list_vendor_contacts` (the endpoint `apps/core/README.md` already plans; the mock
    already serves `GET /developer/v1/vendors/{id}/contacts`).
  - It writes one ledger document of a new kind, `onboarding_record`, with channel `vendor_master`
    and `received_at` set to Dialback's own clock at that moment. For each contact phone number, it
    writes one visible observation with party `vendor` and role `unknown`.
  - A contact whose `updated_at` is more than one day after its `created_at` was edited after
    onboarding, and is left out.
  - The record is written once per Ramp vendor ID, ever. A later sync never adds to it or rewrites
    it.
  - Vendors in the dataset's first import get no onboarding record. Their contacts may hold years of
    edits.
  - Names and emails are never stored, only the numbers.
  - The record also stores the Ramp user who created the vendor, from Ramp's audit log, or null when
    the log doesn't say.
- **D2. The last resort in `select()`.** With the setting `onboarding_fallback = true`, and only when
  today's rule picks nothing, a number becomes eligible as the onboarding pick if all of these hold:
  - a visible sighting of it is in an `onboarding_record` document;
  - that document's `received_at` is at least `min_age_days` (90) before the cutoff;
  - its first visible sighting is outside the change context. With no change request recorded,
    everything is change context (spec 0021), so nothing is picked;
  - every visible sighting labels it the vendor's line (party `vendor`, not a fax);
  - it is not a near miss of an older number.

  The relaxed parts, for this pick only: one source (the onboarding record) instead of two kinds,
  and `vendor_master` counts.

  More than one eligible number is ranked as today: oldest first, then role, then E.164.

  The selection's outcome is `picked`, with `source_types = ["vendor_master"]`, which marks it as
  the onboarding pick. With the setting off, `select()` is exactly today's: same output, same
  `rule_version`.
- **D3. Versions and the flag.**
  - With the setting on, `rule_version` becomes `ledger-select-v2` and `rule_settings` records
    `onboarding_fallback: true`, so a replay knows which rule ran.
  - The setting comes from a new flag, `DIALBACK_ONBOARDING_FALLBACK`: `off` or `on`, safe default
    `off`, read in `packages/ledger/src/dialback_ledger/flags.py`.
  - Lab runs keep it `off` unless a scenario sets it.
- **D4. Everything downstream is unchanged.**
  - The pick is an ordinary ledger selection and contact point, so the voice service's dial path,
    the call script, V01 and every decision rule work as today.
  - Verified still needs every V rule, including V07, which needs the recorded change request. Two
    people still release (R5).
- **D5. Same-person check.** The onboarding pick is refused (`no_provable_number`, reason
  `onboarding_same_person`) when either of these holds:
  - the Ramp user who created the vendor record is the user who made this bank change;
  - either one is unknown.

  The change's author is the existing `changed_by_ramp_user_id`. This mirrors R5's
  segregation of duties for the number itself.
- **D6. What a person sees.** The lineage page labels an onboarding pick "Number on file when this
  vendor was set up, recorded on <date>", next to today's explanation of why no other number
  qualified.

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:**
  - `vendor_master` in `SourceType`;
  - an optional `onboarding_fallback` boolean in `RuleSettings`;
  - `onboarding_same_person` as a value of the free-text top-level `reason`, which needs no schema
    change.
- **`openapi/dialback.yaml`:** no change. `SelectionView` already carries the selection document.
  The new source type flows through the regenerated types.
- **The ledger enum `ledger.document_kind` gains `onboarding_record`,** and `ledger.documents`
  gains a nullable `created_by_ramp_user_id`. This is a ledger migration, owned by Inesh, in the same
  PR.

## Acceptance

- [ ] **O1.** A vendor first seen after the dataset's first import gets exactly one
  `onboarding_record`, holding its contacts' phone numbers as visible vendor observations, with
  `received_at` from our clock. Contacts edited more than a day after creation are left out. Names
  and emails are not stored.
- [ ] **O2.** A later sync, a contact edit or a re-import never adds to an `onboarding_record` or
  rewrites it. Vendors in the first import get none.
- [ ] **O3.** With `DIALBACK_ONBOARDING_FALLBACK=off`, `select()` returns byte-for-byte today's
  document for every existing test and frozen case, with `rule_version = ledger-select-v1`.
- [ ] **O4.** With `on`, a case where today's rule picks a number picks the same number. The
  fallback never overrides a provable pick.
- [ ] **O5.** With `on` and no provable number, an onboarding number recorded at least 90 days before
  the cutoff is picked with `source_types = ["vendor_master"]`. One recorded 89 days before is not.
- [ ] **O6.** The fallback refuses a number in each of these cases:
  - its first visible sighting is in the change context;
  - it is labeled a fax line, or another party's;
  - it is a near miss of an older number;
  - no change request is recorded.

  In each case, the outcome is `no_provable_number`.
- [ ] **O7.** The fallback is refused with `onboarding_same_person` when the vendor's creator is the
  bank change's author, or either is unknown.
- [ ] **O8.** A number on file from an ordinary `vendor_master_record` (not an onboarding record) is
  still shown and never counted.
- [ ] **O9.** The pick dials through today's voice path. V01 holds. A call ending with every V rule
  met, and a recorded change request, is Verified and then needs two people to release.
- [ ] **O10.** The lineage page shows D6's label for an onboarding pick.

The demo

- [ ] **M1.** The demo seed gains a fictional vendor, Dunmore Paper Co, with no documents except an
  onboarding record dated 2022 and a change request. `dialback-ledger demo load --phone
  dunmore=+1...` puts the team phone in that record at run time; it is never committed.
- [ ] **M2.** With the flag on, `demo change --vendor dunmore` calls the team phone and can end
  Verified. With it off, the same case gets no call and goes to a person, as today.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| O1, O2 | `apps/core/tests/test_onboarding_record.py` |
| O3 | the existing `packages/ledger` suite and the frozen-corpus gate, with the flag off |
| O4 to O8 | `packages/ledger/tests/test_select_onboarding.py` (one test per item, plus a property test that the fallback never fires when today's rule picks) |
| O9 | `apps/core/tests/test_onboarding_pick_call.py` |
| O10 | `apps/web`: `pnpm typecheck`, `pnpm build`, and a screenshot |
| M1, M2 | `apps/core/tests/test_demo_dunmore.py`, and a beat in `scripts/dev/demo_rehearsal.py` |

## Build plan

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 1 | Contracts, plus the ledger migration (the document kind, the creator column) | all owners; migration Inesh | | |
| 2 | `select()` fallback, the setting, the flag, `rule_version` v2 (selection rule: planned in plan mode) | Inesh | 1 | O3 to O8 |
| 3 | `RampPort.list_vendor_contacts`, the onboarding record at first sight, the vendor's creator from the audit log | Abhi | 1 | O1, O2 |
| 4 | The lineage label | web owners | 1 | O10 |
| 5 | Dunmore in the mock and ledger demo seeds; the rehearsal beat | Sami, with Abhi and Inesh | 2, 3 | M1, M2, O9 |

PRs 2, 3 and 4 run in parallel after PR 1.

## Out of scope

- Verifying the onboarding number against an independent source (A1; specs 0041 and 0042).
- Holding a brand-new vendor's first payment (spec 0002 Q2).
- Onboarding records for vendors in the first import.
- Sharing anything across customers (spec 0045, roadmap).
- Mailbox intake. Live cases need a recorded change request for any pick, today's rule included.

## Open questions

- **Q98. Who set up the vendor.** Does Ramp's audit log name the user who created a vendor record,
  and does a vendor's own portal submission show as a distinct actor? Until it does, D5 refuses the
  fallback (unknown creator).
- **Q99. The 90 days.** It reuses `min_age_days`. A longer wait for onboarding numbers would narrow
  A2 and A3, at the cost of fewer calls.
- **Q100. A vendor's own portal submission.** Should numbers the vendor typed into Ramp's vendor
  portal count the same as numbers a clerk typed? Today they are treated the same.
- **Q101. Collisions noticed while writing this.** Spec 0045 (PR #93) uses Q87 to Q97, and spec
  0046 (`sami/guided-search`) uses Q87 to Q91. One of them needs renumbering before either merges.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
scripts/db/test.sh
cd packages/ledger && uv run pytest
cd apps/core && uv run pytest
cd apps/ramp-mock && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
uv run python scripts/dev/demo_rehearsal.py
```
