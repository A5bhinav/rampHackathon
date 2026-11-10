# 0047: The vendor's onboarding phone number, as a trusted fallback

**Owner:** Sami  **Slice:** money path (selection) and evidence  **Target day:** Nov 10, 2026. Sami set
this on Nov 9, overriding the freeze dates in AGENTS.md.
**Builds on:** 0021 (the selection rule), 0002 (Ramp sync, first sight). **Replaces, for now:** the
shared-profile route of spec 0045 (PR #93).
**Needs sign-off from:** Inesh (ledger: the selection rule, the source type, the demo seed), Abhi
(core: the contacts read at first sight; the mock), and every contract owner.

## The assumption

**Onboarding is trusted.** The phone numbers a vendor record had when it was set up in Ramp are the
vendor's real numbers. Dialback already makes the same assumption about the original bank account
on file, which also comes from onboarding: Dialback checks only changes to it.

**Known limit, the only one:** if onboarding itself was compromised (a fraudster or an insider set
the vendor up with their own number), a call to the onboarding number reaches them. Verifying
onboarding (specs 0041 and 0042) is on the roadmap. Nothing below adds guards against a compromised
onboarding: that would contradict the assumption.

## Goal

Today a number is callable only if this customer's own documents show it in two kinds of source, at
least 90 days before the change (spec 0021). All email counts as one kind, so a vendor that only
ever emails can never be proven, and its changes go to a person. The number on file is shown, never
counted.

When this spec is done, each vendor record keeps a frozen **onboarding record** of the phone numbers
it was set up with. When today's rule picks no number, the case calls the onboarding number. Today's
rule still wins whenever it picks one. The record is per customer and never shared.

Rules served:

- **R2:** the onboarding record predates the change, so its number can't come from the change
  request.
- **R3:** code picks.
- **R7:** with no onboarding number, nothing is picked and a person decides, as today.
- R1, R4, R5, R6, R8 and R9 are unchanged.

## Decisions

- **D1. What counts as an onboarding number.**
  - Onboarding numbers are a vendor record's Ramp contact phone numbers that were created within one
    day of the vendor record itself and never edited afterwards (`updated_at` within one day of
    `created_at`).
  - A contact added or edited later is not an onboarding number. A contact change is the usual first
    step of a takeover, not part of onboarding.
  - This works for vendors set up long before Dialback, so no "first import" marker is needed.
- **D2. Writing the onboarding record.**
  - The first time the core syncs a vendor record it has never seen in this dataset, it reads the
    record's Ramp contacts once, through a new `RampPort.list_vendor_contacts`. The mock already
    serves `GET /developer/v1/vendors/{id}/contacts`, with `created_at` and `updated_at`. It reads
    the vendor's own `created_at`, which the mock returns and `RampVendor` gains.
  - It calls a new ledger writer, `write_onboarding_record`, which stores one document. The
    document has the new source type `onboarding` and `received_at` from Dialback's clock. It holds
    one visible observation per onboarding number: party `vendor`, role `unknown`, no quote, so no
    names are stored.
  - The record is written even when it holds no numbers, and never retried or rewritten. A
    partial unique index allows one onboarding record per dataset and vendor.
  - Only the writer creates `onboarding` documents. Ingestion refuses that source type. A database
    trigger enforces `received_at = app.now()` for them, except in demo datasets, whose seed sets
    dates.
  - Records are written whether or not the flag is on, so they exist when it is turned on.
- **D3. The fallback in `select()`.** With the setting `onboarding_fallback = true`, and only when
  today's rule picks nothing, a number is eligible if all of these hold:
  - it is in this vendor's onboarding record;
  - the record's `received_at` is before the cutoff (it predates the change);
  - no document **outside** the change context labels it a fax line or another party's line. Labels
    inside the change context are ignored for this pick, so a change email can't knock out the real
    number (FINDINGS A1);
  - it is not a near miss of an older number.

  "Picks nothing" means the outcome `no_provable_number` only, never `withheld` (spec 0079). A
  withheld case is one where the change context would change who is called; falling back to the
  onboarding number there would move the call at the system level.

  There is no age requirement and no recorded change request needed: the record predates the change
  (the assumption).

  With several eligible numbers, the order is E.164.

  The pick's candidate entry is `eligible: true`, `rank: 1`, `reasons: []`, `source_types:
  ["onboarding"]`. The outcome is `picked` with `source_types = ["onboarding"]`.
- **D4. Versions and the flag.**
  - New flag `DIALBACK_ONBOARDING_FALLBACK`, `off` or `on`, safe default `off`, read in
    `packages/ledger/src/dialback_ledger/flags.py`. It goes in the AGENTS.md flag table.
  - With `off`, `select()` and the risk list drop every `onboarding` document and its sightings
    before doing anything else. Output is byte-for-byte today's, including `rule_version =
    ledger-select-v1`, `rule_settings` and `inputs_sha256`.
  - With `on`, `rule_version` is `ledger-select-v2` per call, and `rule_settings` gains
    `onboarding_fallback: true`.
- **D5. Everything downstream is unchanged.**
  - The pick is an ordinary ledger selection and contact point, so the voice service's dial path,
    the call script, V01 and every decision rule work as today.
  - A vendor's "no" gives Not verified, and the change is blocked.
  - Verified still needs every V rule, including V07, which needs a recorded change request. Without
    one, a "yes" goes to a person.
  - Two people still release (R5).
- **D6. What a person sees.** The API gives the source type `onboarding` the label "Number on file
  when this vendor was set up". The lineage page and the timeline show it like any other source type.

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:** `onboarding` in `SourceType`, and an optional
  `onboarding_fallback` boolean in `RuleSettings`. `vendor_master` stays "never a source type".
- **`openapi/dialback.yaml`:** the label for the new source type in the selection view's text, and
  regenerated types.
- **Ledger migrations, owned by Inesh:**
  1. `ledger.source_type` gains `onboarding`;
  2. a second file, since an enum value and an index can't share one transaction, adds the partial
     unique index (one onboarding record per dataset and vendor) and the `received_at` trigger.

## Acceptance

- [ ] **O1.** The first sync of a vendor writes exactly one onboarding record. It holds the phone
  numbers of contacts created within one day of the vendor record and never edited, as visible
  vendor observations with no quote. A record with no numbers is still written.
- [ ] **O2.** A later sync, a contact change or a second writer never adds to the record or rewrites
  it; the unique index refuses a second one. Ingestion refuses the `onboarding` source type. Outside
  demo datasets, the database refuses an onboarding record whose `received_at` isn't the current
  time.
- [ ] **O3.** With the flag off, `select()` returns byte-for-byte the same document as origin/main
  for every existing test and frozen case, even when onboarding records exist. Checked against a
  golden snapshot taken from origin/main.
- [ ] **O4.** With the flag on, a case where today's rule picks a number picks the same number.
- [ ] **O5.** With the flag on and nothing provable, the onboarding number is picked, with the
  candidate fields in D3, even when no change request is recorded.
- [ ] **O6.** A change email that labels the onboarding number a fax line or another party's line
  doesn't stop the pick. An older document outside the change context that does so stops it. So
  does a near miss of an older number.
- [ ] **O7.** A record written after the cutoff is never used.
- [ ] **O8.** The pick dials through today's voice path, and V01 holds.
  - The vendor saying "no" gives Not verified.
  - A "yes" with every V rule met and a recorded change request gives Verified, then two people
    release.
  - A "yes" with no recorded request gives Needs review.
- [ ] **O9.** The lineage page and timeline show the API's label for an onboarding pick.

The demo

- [ ] **M1.** The demo seed gains a fictional vendor, Dunmore Paper Co, with no documents except an
  onboarding record (seeded date) holding its number. `dialback-ledger demo load --phone
  dunmore=+1...` puts the team phone there at run time; it is never committed. In demo datasets,
  onboarding records come from the ledger demo seed, and the core's first-sync writer skips them,
  so the phone swap works.
- [ ] **M2.** With the flag on, `demo change --vendor dunmore` calls the team phone.
  - A "no" blocks the change.
  - A "yes" after `dialback-ledger demo request --vendor dunmore` can end Verified.

  With the flag off, the same case gets no call and goes to a person, as today.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| O1, O2 | `apps/core/tests/test_onboarding_record.py`, `packages/ledger/tests/test_onboarding_writer.py`, and the index and trigger cases in `scripts/db/test.sh` |
| O3 | `packages/ledger/tests/test_select_golden.py` (a snapshot from origin/main), plus the existing suite and frozen-corpus gate with the flag off |
| O4 to O7 | `packages/ledger/tests/test_select_onboarding.py` (one test per item, and a property test that the fallback never fires when today's rule picks) |
| O8 | `apps/core/tests/test_onboarding_pick_call.py` |
| O9 | `apps/web`: `pnpm typecheck`, `pnpm build`, and a screenshot |
| M1, M2 | `apps/core/tests/test_demo_dunmore.py`, and a beat in `scripts/dev/demo_rehearsal.py` |

## Build plan

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 1 | Contracts, plus the two ledger migrations | all owners; migrations Inesh | | |
| 2 | `write_onboarding_record`, ingestion refusing the source type, the `select()` fallback, flag-off filtering, the setting, `rule_version` v2 (the selection rule: planned in plan mode) | Inesh | 1 | O2 to O7 |
| 3 | `RampPort.list_vendor_contacts`, `RampVendor.created_at`, the onboarding record at first sync | Abhi | 1, 2 | O1 |
| 4 | The label in the API and the web | Abhi (API), web owners | 1 | O9 |
| 5 | Dunmore in the mock and ledger demo seeds (with `phones.ar` for the swap); the rehearsal beat | Sami, with Abhi and Inesh | 2, 3 | M1, M2, O8 |

## Out of scope

- Verifying onboarding (the assumption; specs 0041 and 0042).
- Holding a brand-new vendor's first payment (spec 0002 Q2).
- Refreshing the onboarding record later, or checking that the number is still in service (FINDINGS
  A7).
- Sharing anything across customers (spec 0045, roadmap).
- Mailbox intake. Today's rule, and Verified, still need a recorded change request.

## Open questions

- **Q98. Contacts for vendors set up long ago.** Real Ramp's contact timestamps may not survive
  migrations or edits. If `created_at` or `updated_at` is missing, the contact is left out.
- **Q99. A vendor's own portal submission.** Should numbers the vendor typed into Ramp's vendor portal
  count the same as numbers a clerk typed? This spec treats both as onboarding.
- **Q100. Numbering.** This spec starts at Q98 because spec 0045 (PR #93, now closed) had taken Q87 to
  Q97. Spec 0046 (`sami/guided-search`) keeps Q87 to Q91, so nothing collides now.

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
