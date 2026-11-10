# 0045: Shared vendor profile, an ordered list of trusted numbers, and the vendor portal

**Owner:** Sami  **Slice:** call and adversary, with money-path changes listed under Build plan
**Target day:** Nov 10, 2026. Sami set this on Nov 9, overriding the freeze dates in AGENTS.md.
**Builds on:** 0021 (the selection rule), 0042 (the pattern for a second kind of call). **Changes:**
ADR 0015 (PR #92) deferred shared trust; this spec adopts it for phone numbers, under the rules
below.

## Goal

Today each customer proves a vendor's phone number on its own, from 90 days of its own documents
(spec 0021). A customer new to a vendor gets no call, and a number that stops working is noticed
only when a call fails. When this spec is done:

- **One shared profile per real vendor** links that vendor's records at every customer, by a tax-ID
  fingerprint that Ramp computes. The mock plays Ramp.
- **Each profile has an ordered list of trusted phone numbers:** onboarding numbers first, then
  numbers confirmed by a call to a trusted number, then the customer's own age-checked number last.
- **A bank-change case calls down the list.** It moves to the next number only when a call doesn't
  connect, or reaches someone who isn't the vendor. If no call passes, a person decides, with the
  profile in front of them.
- **Phase 2, the vendor portal:** a vendor asks to replace a trusted number, and a keypad
  confirmation call to a trusted number makes the change.

Rules served: R1 and R5 are unchanged. R2: every shared number was trusted before the change, so it
cannot come from the change request. R3: code builds the list, classifies each call and decides.
R4 and R8: both calls disclose first and run the speech guard. R6: every profile change is
hash-chained. R7: anything unclear goes to a person. R9: no text from a call or a document changes
the list.

## Assumptions

- **A1. Onboarding is checked elsewhere.** The phone numbers a vendor had when a customer first
  added it in Ramp are assumed valid, checked by Ramp's onboarding, which is outside Dialback.
  Dialback builds no onboarding check.
- **A2. The known limit that follows from A1.** If onboarding is fooled (for example, a sham
  business adds "Acme Fasteners LLC" with Acme's tax ID and its own phone number), that number joins
  Acme's list for every customer. Three things keep it from being called first:
  - the list puts older numbers first (D5), so a later arrival ranks behind the real vendor's
    numbers;
  - a member linked within `waiting_days` sends the case to review (D9);
  - a member whose legal name doesn't match the profile isn't linked (D1).

## Decisions

- **D1. Linking.**
  - The Ramp mock computes `tax_id_fingerprint = HMAC-SHA256(DIALBACK_NETWORK_KEY, normalized tax
    ID)` and returns it on the vendor representation. It never returns the tax ID itself.
  - The core stores the fingerprint in `app.vendors.tin_hash` on sync.
  - A vendor joins the profile with the same fingerprint only if its normalized legal name equals
    the profile's (legal suffixes such as LLC and Inc are ignored). Otherwise it joins no profile,
    and an alert goes to a person.
  - Nothing else links: no trade names, domains or phone numbers.
  - A change of `tin_hash` on a vendor unlinks it and relinks it, which dates a new link (D9).
  - Lab datasets and retired datasets never link. Demo datasets link only with each other.
  - With `DIALBACK_RAMP=sandbox`, no fingerprint arrives and nothing links (Q87).
- **D2. Onboarding numbers (tier 1, shared).**
  - These are a member vendor's Ramp contact phone numbers created within one day of the vendor
    record. The core reads them through a new `RampPort.list_vendor_contacts` (the endpoint
    `apps/core/README.md` already plans).
  - A contact added or edited later is not an onboarding number. A contact change is the usual
    first step of a takeover.
  - `trusted_since` is the vendor record's creation time at that customer.
- **D3. Numbers confirmed by a call (tier 2, shared). Phase 2.** These are added by a confirmed
  portal request (D13). Nothing else adds them.
- **D4. The customer's age-checked number (tier 3, this customer only).** This is the ledger's own
  pick for the case under spec 0021, unchanged. It is never shared with other members.
- **D5. Order.** By tier, then oldest `trusted_since` first, then E.164 order. A number must be
  trusted before the case's cutoff. The cutoff is the earliest of the change in Ramp, the recorded
  change request and the first message from a lookalike domain, as in spec 0021. A number may appear
  only once, at its best position. A `flagged` number (D7) is left out until a person restores it.
- **D6. Calling down the list.** The case calls the list in order, at most 3 attempts per case (the
  existing cap). Each call's result falls into one of three classes, decided by code from the
  existing call facts:

  | Class | When | Then |
  |---|---|---|
  | **No connect** | No answer, voicemail, busy, failed, or a hang-up before any consent answer. A spoken "no" to consent is not a no-connect: it stops, as today (NR02) | Call the next number |
  | **Not the vendor** | Consent given, and the company the person named matches none of the vendor's names (not a partial match) | Mark the number `flagged`, then call the next number |
  | **Reached the vendor** | Anything else | Stop and decide, as today. A "no" or wrong digits never moves on |

  When the list runs out, or the attempts do, the case goes to Needs review with the profile panel
  (W1).
- **D7. Flagged numbers.** A number flagged in D6 leaves every member's list, because it is probably
  reassigned. A person with the approver role can restore it from the profile panel. The profile's
  chain records both events.
- **D8. Profile wins over the ledger, with guards.** When the customer's own ledger excludes a
  shared number (for example, `first_seen_in_change_context`), the shared number is still called.
  It was trusted before the cutoff, so it can't come from this change request (R2). The guards
  are D5's cutoff and D9.
- **D9. A recent change sends the case to review.** `waiting_days = 7`. A new tri-state fact,
  `profile_changed_recently`, is true if, within `waiting_days` before the cutoff, any of these
  happened: a number was added, flagged, restored, set to retiring or retired; or the case's own
  vendor was linked to the profile. It is unknown when the case is not on a profile. It feeds a new
  rule, `NR22_profile_changed_recently`. The engine version becomes `decision-1.1.0`.
- **D10. No change request on record.** The call still happens. V07 can't hold without the request,
  so the outcome is Needs review with the call's answers attached. The decision engine is
  unchanged (author's call: Q89).
- **D11. How a pick reaches the voice service.**
  - Each attempt's pick is written as a `ledger.number_selections` row with `rule_version =
    profile-select-v1`, source type `shared_profile` (tiers 1 and 2) or the ledger's own (tier 3),
    and a `profile` block: profile ID, tier, list position, `trusted_since`, how the number became
    trusted, `profile_changed_recently`, and the whole ordered list as last-four digits.
  - The contact point is the ledger's own row for that dataset, vendor and number. It uses the
    ledger's ID (`uuid5(vendor_id:e164)`) and is created if missing. An existing row's status, source
    types or first-seen date are never changed.
  - The voice service dials the selection named by the case's latest `number_resolved` event, not
    the newest row. Its opt-out check reads a real opt-out only, never a ledger exclusion.
  - V01 keeps its ID; its text becomes "We dialed the number Dialback chose for this change."
  - `decide()` reads the `profile` block from the selection that `number_resolved` names.
- **D12. Evidence.**
  - Profile events go in a new append-only table, `profile.events`, hash-chained per profile with
    the same canonical JSON and SHA-256 as `audit` (reusing `audit.jcs`).
  - Events: `profile_created`, `member_linked`, `member_unlinked`, `number_listed`,
    `number_flagged`, `number_restored`, plus phase 2's request events.
  - The existing verify job also verifies profile chains.
  - Case evidence is unchanged, except that `number_resolved` names the selection, as today.
  - Integers and strings only.
- **D13. Phase 2: the vendor portal and the confirmation call.**
  - A vendor user asks to replace trusted number T with new number N.
  - The voice service calls the profile's oldest trusted number still in service, which may be T,
    with its own script: disclosure, consent, the company's name, "Did your company recently ask
    to change the phone number on its vendor profile?", then "Please type the last four digits of
    the new number on your keypad, then press pound." Pound is required, to match spec 0043.
  - **`confirmed`** needs all four: consent, the company matches, a "yes", and the digits equal N's
    last four. Then N joins tier 2 and T becomes `retiring` for `waiting_days`.
  - **`denied`:** a clear "no". It raises an alert on the profile that every member's next case
    sees (it sets D9's fact).
  - **`not_confirmed`:** anything else. Nothing changes.
  - The portal shows the requester only "received" or "closed", never the outcome or why. An
    attacker holding the login learns nothing to retry with.
  - Storage, routes, guard and caps follow 0042's pattern, in `voice.profile_calls`.
    Confirmation calls belong to the vendor's demo dataset, so `demo reset` clears their caps.
  - Vendor users live in `profile.vendor_users`. The sign-up trigger skips them, so they get no
    clerk role. Restrictive row-level security covers `app`, `ledger`, `voice` and `audit`, and
    every customer endpoint answers 403 by an explicit check.
- **D14. What each side sees.**
  - The customer's reviewer sees the profile panel: the ordered list (last four digits, tier, how
    and when each number became trusted, its call history as dates and outcomes), the vendor's
    legal name history, its bank accounts as last four digits with first and last seen, and this
    customer's own documents through the existing lineage page.
  - A reviewer never sees another customer's name, documents, answers or rule codes.
  - Until a tenant model exists, row-level security does not enforce this between customers. Only
    the pages do (Out of scope).
- **D15. The flag.** `DIALBACK_PROFILE` is `off` or `on`, safe default `off`, read in
  `apps/core/src/dialback_core/flags.py`. With `off`, no profile is read or written and selection
  and calling are exactly today's.

## What a profile holds

| Group | Contents | Shared with every member customer? |
|---|---|---|
| Identity | Profile ID, tax-ID fingerprint (never the tax ID), legal name and its history, entity type | Yes |
| Members | Which customers' vendor records are linked, and when | No; each customer sees only its own link |
| Phone list | Each number: E.164, role, tier, how and when trusted, state (listed, flagged, retiring, retired), call history as dates and outcomes | Yes, as last four digits on customer pages |
| Bank accounts | Fingerprint, routing, last four, bank name, first and last seen, current or previous | Last four and dates only |
| Documents | Emails, invoices, contracts, W-9s | Never; each stays in its customer's ledger |
| Events | The hash-chained profile log | Ramp operators; customers see the facts above |

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:** `shared_profile` in `SourceType`, and the optional `profile`
  object (D11). `profile_unavailable` is a value of the selection's top-level `reason`, which is
  free text, so the schema needs no change for it.
- **`openapi/dialback.yaml`:**
  - `GET /datasets/{datasetId}/cases/{caseId}/profile`, the profile panel, and `POST
    .../profile/numbers/{e164}/restore`;
  - in phase 2, `GET /vendor/me`, `GET /vendor/profile`, `POST /vendor/requests` and `GET
    /vendor/requests`;
  - regenerated types.
- **`openapi/internal.yaml`, phase 2:** `POST /profile-calls`, and `POST
  /internal/profile-calls/{id}/ended`.
- **The ledger enum `ledger.source_type` gains `shared_profile`.** This is a ledger migration,
  owned by Inesh, in the same PR.

Not contracts:

- `profile.events` has its own table and schema, outside the case evidence envelope.
- The mock's `tax_id_fingerprint` lives in the mock's own model (Q87).

## Acceptance

Linking and the list

- [ ] **L1.** The mock returns `tax_id_fingerprint` for every vendor that has a tax ID. Equal
  normalized tax IDs give equal fingerprints across businesses. The tax ID is never returned.
- [ ] **L2.** The core stores the fingerprint in `tin_hash`. Vendors in different datasets with the
  same fingerprint and matching legal names are members of one profile. A name mismatch links
  nothing and raises an alert. Lab and retired datasets never link.
- [ ] **L3.** A member's onboarding numbers, meaning contacts created within one day of the vendor
  record, are listed in tier 1. Contacts created later never are.
- [ ] **L4.** The list is ordered by tier, then `trusted_since`, then E.164. It holds only numbers
  trusted before the cutoff, never a flagged number, and each number only once.
- [ ] **L5.** Every link, unlink, listing, flag and restore appears in the profile's chain, and the
  verify job checks that chain.

Bank-change calls

- [ ] **K1.** With `DIALBACK_PROFILE=off`, every existing test passes. The one exception is the
  engine-version assertion in `packages/decision/tests/test_engine.py`, which moves to
  `decision-1.1.0` with NR22. That test is not wrong; the version changes on purpose.
- [ ] **K2.** With `on`, a case on a profile calls the first number on the list. A no-connect
  moves to the next number on the next attempt, with a new selection and a new `number_resolved`.
  V01 holds for the number actually dialed.
- [ ] **K3.** A call that reaches someone naming an unrelated company flags that number, removes it
  from every member's list, and moves to the next number.
- [ ] **K4.** A call that reaches the vendor is decided as today. A "no", wrong digits or a partial
  company name never moves on to another number.
- [ ] **K5.** When the list or the 3 attempts run out, the case is Needs review and the profile
  panel shows every attempt.
- [ ] **K6.** A shared number is called even when this customer's ledger excludes it (D8), and the
  voice service doesn't treat the exclusion as an opt-out.
- [ ] **K7.** A customer with no history for the vendor gets a call to the shared list. With no
  change request recorded, the outcome is Needs review, never Verified (D10).
- [ ] **K8.** If the profile can't be read, no profile number is called. The selection is
  `no_provable_number` with reason `profile_unavailable`, and a person decides (R7).

The decision

- [ ] **N1.** `NR22_profile_changed_recently` fires when any D9 event falls within 7 days before the
  cutoff, even if every V rule holds. The fact is unknown for a case that isn't on a profile, and
  NR22 does not fire then.

The dashboard

- [ ] **W1.** A case on a profile shows the profile panel (D14). No other customer's name,
  documents, answers or rule codes appear on any page.
- [ ] **W2.** An approver can restore a flagged number. A clerk can't.

The demo

- [ ] **M1.** `dialback-core demo network [--phone acme=+1...]` seeds four fictional customers that
  pay Acme, as one demo group:
  - Fictional Co;
  - Harbor Supply Co, with two years of Acme documents;
  - Pinecrest Clinic, created last week, with none;
  - a sham, Quickstart Holdings, created 3 days ago, whose "Acme Fasteners LLC" has Acme's tax ID
    and the number +1 206 555 0199.

  `--phone` makes Acme's oldest onboarding number a team phone at run time; it is never committed.
  `demo change` and `demo status` take `--dataset <ref>`. `demo reset` retires the whole group.
- [ ] **M2.** After `demo network`, Acme's list for Pinecrest starts with the team phone. The sham's
  number ranks last, and any case at Pinecrest within 7 days of the sham's link goes to review
  (NR22).
- [ ] **M3.** A bank change at Pinecrest calls the team phone. Today it would get no call.
- [ ] **M4.** With the first number set to an unanswered line, the case calls the next number.

Phase 2 (only after the items above pass)

- [ ] **V1.** A vendor user sees its profile and can ask to replace a trusted number. It sees
  "received" or "closed", never the outcome. It gets 403 from customer endpoints and reads nothing
  through row-level security, `audit` included.
- [ ] **V2.** The confirmation call runs D13's script. `confirmed` needs all four conditions. A
  "no" raises the alert that sets NR22's fact for every member. Anything else changes nothing.
- [ ] **V3.** Typed digits are never stored or logged. The guard refuses anything it can't read.
  The call result applies once, and only to a pending request.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| L1 | `apps/ramp-mock/tests/test_tax_fingerprint.py` |
| L2 to L5 | `packages/profile/tests/test_link.py`, `test_list.py`, `test_events.py` |
| K1 | the existing suites with the flag `off` |
| K2 to K8 | `apps/core/tests/test_profile_calling.py` (one test per item), `packages/profile/tests/test_classify.py` for D6's classes |
| N1 | `packages/decision/tests/test_rules.py::test_nr22_profile_changed_recently`, `::test_nr22_unknown_off_profile` |
| W1, W2 | `apps/core/tests/test_profile_panel.py`, plus `pnpm typecheck`, `pnpm build` and screenshots |
| M1 to M4 | `apps/core/tests/test_demo_network.py`, and the network beats in `scripts/dev/demo_rehearsal.py` |
| V1 to V3 | `apps/core/tests/test_vendor_portal.py`, `packages/policy/tests/test_profile_script.py`, `apps/voice/tests/test_profile_calls.py`, and the vendor row-level security cases in `scripts/db/test.sh` |

## Build plan

Phase 1 first. Each PR stays under about 400 lines. After PR 1, PRs 2 and 3 run in parallel.

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 1 | Contracts, plus the ledger enum value | all owners; enum Inesh | | |
| 2 | `profile` schema: add it to `apply.sh`, `migrate.py` and the bootstrap. Profiles, members, numbers, events, row-level security. `packages/profile`: link, list, classify, flag and restore, the chain | Sami | 1 | L2 to L5 |
| 3 | Mock fingerprint; `tin_hash` and onboarding contacts on sync (`RampPort.list_vendor_contacts`); tax IDs in the demo seed | Abhi | | L1, L3 |
| 4 | The profile resolver behind the flag; calling down the list in the worker; the voice service dialing what `number_resolved` names and checking real opt-outs only; NR22, its fact, V01's text, the engine bump | Abhi (core, decision), Sami (voice), Inesh (the contact-point helper) | 2, 3 | K1 to K8, N1 |
| 5 | The profile panel and the restore action | Abhi (core), web owners | 2, 4 | W1, W2 |
| 6 | `demo network` and the group handling in the demo commands | Sami | 2 to 5 | M1 to M4 |
| 7 | Phase 2: portal, vendor users, confirmation call | Sami, Abhi, web owners | 6 | V1 to V3 |

## Out of scope

- Onboarding checks (A1).
- Bank details in the portal.
- A notice to the old number.
- Account recovery.
- Mailbox intake.
- ADR 0015's warning signals.
- Promoting a customer's age-checked number into the shared list.
- A tenant model for customers. Pages enforce D14, but row-level security doesn't yet: any customer
  user can still read every dataset, including the `profile` block of other customers' selections.
- Real Ramp's fingerprint (Q87).
- A network-wide call cap per number across customers (Q90).

## Open questions

- **Q87. Real Ramp.** Can Ramp return a network tax-ID fingerprint, or compute one for Dialback?
  Until it can, `sandbox` never links.
- **Q88. Who the phase 2 confirmation call is for.** No single customer applies, and the speech
  guard blocks "verif" words. Proposed disclosure: "calling for the accounts payable teams of your
  customers on Ramp", plus a callback number.
- **Q89. The author's calls, to confirm:**
  - D10: no recorded request still calls, then a person decides;
  - `waiting_days = 7`;
  - the one-day window that defines an onboarding contact;
  - flagged numbers leave every member's list.
- **Q90. A network-wide cap per number.** Today the cap counts per contact point, which means per
  dataset. A real vendor called by every customer could get far more than 3 AI calls in 30 days.
- **Q91. Ownership.** Proposed: `packages/profile` and the `profile` schema owned by Sami, with
  Inesh as partner.
- **Q92. The separate profile-combining work** mentioned on Nov 9 is not in the repo. This spec
  links only by tax-ID fingerprint and legal name; reconcile before PR 2.
- **Q93. The network key in production:** a key-management service and rotation. In the demo it is
  an env var on the mock, and nothing in Dialback holds it.
- **Q94. Phase 2's residual risk.** An attacker who controls the vendor's email can prime the front
  desk to answer the confirmation call and type the digits. After `waiting_days`, every customer
  calls the attacker's number. A longer wait for portal-confirmed numbers, or a second confirmation
  through another trusted number, would narrow this.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
scripts/db/test.sh
cd packages/profile && uv run pytest
cd packages/decision && uv run pytest
cd packages/ledger && uv run pytest
cd apps/voice && uv run pytest
cd apps/core && uv run pytest
cd apps/ramp-mock && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
uv run python scripts/dev/demo_rehearsal.py
```
