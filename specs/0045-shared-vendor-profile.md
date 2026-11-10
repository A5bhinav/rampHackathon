# 0045: Shared vendor profile, an ordered list of trusted numbers, and the vendor portal

**Owner:** Sami  **Slice:** call and adversary, with money-path changes listed under Build plan
**Target day:** Nov 10, 2026. Sami set this on Nov 9, overriding the freeze dates in AGENTS.md.
**Builds on:** 0021 (the selection rule), 0041 (anchors; code on `sami/onboarding-anchors`), 0042
(the pattern for a second kind of call). **Changes:** ADR 0015 (PR #92) deferred shared trust; this
spec adopts it for phone numbers, under the rules below.

## Goal

Today each customer proves a vendor's phone number on its own, from 90 days of its own documents
(spec 0021). A customer new to a vendor gets no call, and a case redials one number until it gives
up. When this spec is done:

- **One shared profile per real vendor** links that vendor's records at every customer, by a tax-ID
  fingerprint that Ramp computes. The mock plays Ramp.
- **Each profile has an ordered list of trusted numbers:** the onboarding anchor first, then (phase
  2) numbers confirmed by a call to a trusted number, then this customer's own proven number.
- **A bank-change case calls down the list, by itself.** It moves on when a call doesn't connect or
  reaches someone who isn't the vendor, and stops as soon as it reaches the vendor. When the tries
  run out, a person decides, with the profile in front of them.
- **Onboarding is checked:** a number a customer entered for the vendor counts only if it matches
  the profile.
- **Phase 2, the vendor portal:** a vendor asks to replace a trusted number, and a keypad
  confirmation call to a trusted number makes the change.

Rules served:

- R1 and R5 are unchanged.
- R2: every shared number was listed before the change, so it cannot come from the change request.
- R3: code builds the list, classifies each call and decides.
- R4 and R8: both calls disclose first and run the speech guard.
- R6: every profile change is hash-chained.
- R7: anything unclear goes to a person.
- R9: no text from a call or a document changes the list.

## Decisions

### The profile

- **D1. Linking.**
  - The Ramp mock computes `tax_id_fingerprint = HMAC-SHA256(DIALBACK_NETWORK_KEY, normalized tax
    ID)` and returns it on the vendor representation. It never returns the tax ID itself.
  - The core stores the fingerprint in `app.vendors.tin_hash` on sync.
  - A profile is keyed by (fingerprint, scope). The scope is `live` for ordinary datasets, or the
    demo group's ID for demo datasets, so demo and real profiles never mix. Lab datasets never link.
  - A vendor joins a profile only if its normalized legal name (ignoring LLC, Inc and similar)
    equals the profile's. Otherwise it joins nothing and an alert goes to a person.
  - Retiring a dataset unlinks its vendors. A change of `tin_hash` unlinks a vendor and relinks it,
    which dates a new link (D9).
  - With `DIALBACK_RAMP=sandbox`, no fingerprint arrives and nothing links (Q87).
- **D2. Tier 1: the onboarding anchor (shared).**
  - When a profile is created, and again whenever a member links while the profile has no anchor,
    spec 0041's `choose_anchor()` runs on the profile's legal name and state. The sources are the
    registry and website fixtures.
  - The tax-ID check (`tax_id_mismatch`) is removed from `CHECKS`, because Dialback never holds a
    tax ID. The fingerprint link (D1) already ties a profile to one tax ID. The other checks stay:
    a registry entry exists, it is active, the name and state match, the domain is at least 365
    days old, and two independent kinds of source agree.
  - Outcome `anchored` lists the anchor in tier 1, with `listed_at` set to when it was anchored.
    Outcome `no_anchor` lists nothing in tier 1.
  - Once anchored, a re-run that gives a different number raises an alert and keeps the old anchor.
- **D3. Onboarding numbers are checked, not trusted.** On a member's first sync, the core reads its
  Ramp contacts through a new `RampPort.list_vendor_contacts` (the endpoint `apps/core/README.md`
  already plans). A contact number that is on the profile's list is fine. Any other number raises
  an alert on the profile ("this onboarding gave Acme a number Acme has never used") and is never
  listed. Contacts are compared, never stored.
- **D4. Tier 2: numbers confirmed by a call (shared).** Phase 2 only (D14).
- **D5. Tier 3: this customer's own proven number (this customer only).** This is the ledger's own
  pick under spec 0021, unchanged. It is never shared.
- **D6. Order.**
  - By tier, then oldest `listed_at` first, then E.164 order.
  - A number must be listed before the cutoff, and appears only once, at its best position.
  - A flagged number (D8) is left out.
  - The cutoff is the ledger selection's own `cutoff`: the earliest of the change in Ramp, the
    recorded change request and the first message from a lookalike domain, as in spec 0021.

### Calling down the list

- **D7. Which number each try calls.** A case on a profile has at most 3 tries, the existing cap.
  - Try 1 calls the first number on the list.
  - A try after a call that **didn't connect** calls the next listed number not yet dialed in this
    case version. When the list runs out, it goes back to the top. Didn't connect means the result
    was `no_answer`, `busy`, `voicemail`, `machine` or `declined`, where `declined` is a hang-up
    before the consent answer.
  - A try after a call that **reached someone who isn't the vendor** (D8) also calls the next number.
  - A try after `failed` or `callback_requested` redials the **same** number, as today. A `failed`
    call can end after consent, with a "no" already said.
  - A call that **reached the vendor** (`answers_recorded`, not D8) is decided as today. A "no",
    wrong digits or a partial company name never moves on to another number.
  - When the tries run out, the case goes to Needs review, and the profile panel shows each try.
- **D8. "Not the vendor."**
  - Code in `packages/profile` classifies a finished `answers_recorded` call as not the vendor when
    consent was given, a company was named, and the named company shares no significant word with
    any name of any member's vendor record or the profile's legal-name history. Suffixes like LLC,
    Inc and Co, and common words like "the" and "company", don't count.
  - The core classifies before applying `call_ended`. It then flags the number: a tier 1 or tier 2
    number leaves every member's list, and a tier 3 number leaves this customer's list.
  - The flag is chained. An approver can restore the number from the profile panel.
- **D9. A recent change sends the case to review.**
  - `waiting_days = 7`.
  - A new tri-state fact, `profile_changed_recently`, is true when any of these has an
    `effective_at` between `waiting_days` before the cutoff and the moment the number is selected:
    a number listed, flagged, restored, set to retiring or retired; or this case's own vendor
    linked or relinked.
  - The fact is unknown when the case isn't on a profile. If the case is on a profile but the fact
    can't be read, it is treated as true.
  - It feeds a new rule, `NR22_profile_changed_recently`. The engine version becomes
    `decision-1.1.0`.
  - `effective_at` is the time of the event. Only `demo network` may backdate it, and only for demo
    datasets, so a demo profile looks settled.
- **D10. Profile wins over the ledger.** A shared number is called even when this customer's ledger
  excludes it (for example, `first_seen_in_change_context`). It was listed before the cutoff, from
  an anchor or (phase 2) a call, so it can't come from this change request (R2). A sham can't put a
  number in tier 1, because the anchor comes from sources it doesn't supply.
- **D11. No change request on record.** The call still happens. V07 can't hold without the request,
  so the outcome is Needs review, with the call's answers attached. The decision engine is
  unchanged (Q89).

### How it plugs in

- **D12. The case state machine** (redefine `app.eval_guard` and `app.apply_case_event` the way
  0008 and 0011 did, and regenerate `docs/state-machine.md`):
  - **Profile cases on a retry ask for a new number.** On a case whose selection is a profile pick,
    `retry_timer_fired` stays in `retry_wait` and enqueues `select_number` keyed
    `{case}:select:v{N}:a{attempt}`. Try 1 keeps today's key `{case}:select:v{N}`. Other cases are
    unchanged and enqueue `place_call` directly.
  - **New move:** `retry_wait` to `calling` on `number_resolved [number_selected]`, which enqueues
    `place_call`, as the move from `held` does.
  - **"Not the vendor" joins the retry path.** `call_ended [answers_recorded]` now also needs
    `not_vendor` false or absent. New guards `call_ended [not_vendor_tries_left]` go to
    `retry_wait`, and `[not_vendor_tries_exhausted]` go to `needs_review`.
  - **Delays.** A retry that changes number waits `next_number_delay_seconds` (default 120; demo
    datasets 30). A retry of the same number keeps today's delay.
  - **Event keys** include the try number, so a second selection in one version is never dropped as
    a duplicate.
- **D13. How a pick reaches the voice service.**
  - Each try's pick is a `ledger.number_selections` row:
    - `rule_version = profile-select-v1`;
    - source type `shared_profile` for tiers 1 and 2. A tier 3 pick reuses the ledger's own
      selection row;
    - `cutoff` and `requested_cutoff` taken from the ledger's selection;
    - `rule_settings` set to the ledger's settings;
    - `candidates` set to the ordered list (rank = position);
    - a `profile` block: profile ID, tier, try number, numbers already dialed,
      `profile_changed_recently`, and a snapshot of the list;
    - `inputs_sha256` taken over that canonical snapshot, so a replay reads everything from the
      document.
  - A tier 1 or 2 pick upserts the ledger's own contact point row. It uses the ledger's ID
    (`uuid5(vendor_id:e164)`), `status = eligible`, `first_seen_at = listed_at` and source types
    `{shared_profile}`, and never changes an existing row.
  - **The voice service dials the selection named by the case's latest `number_resolved` in the
    current version,** read from `audit.evidence_events` the way `apps/core/.../api.py` already
    resolves it. It does not dial the newest row.
  - The ledger status check (`eligible`) still applies to tier 3 picks and is skipped for tier 1 and
    2 picks. No real opt-out store exists yet (Q95).
  - V01's ID and text stay as they are. A profile pick is still a selection in the Trust Ledger's
    table.
  - The decide handler reads the `profile` block for `picks[-1]` through a port, as
    `LedgerChangeContext` does. `decide()` stays pure.
- **D14. Phase 2: the vendor portal and the confirmation call.**
  - A vendor user asks to replace trusted number T with new number N.
  - The voice service calls the profile's first listed number in service, with its own script:
    1. disclosure;
    2. consent;
    3. the company's name;
    4. "Did your company recently ask to change the phone number on its vendor profile?";
    5. "Please type the last four digits of the new number on your keypad, then press pound." Pound
       is required, to match spec 0043.
  - **`confirmed`** needs all four: consent, the company matches, a "yes", and the digits equal N's
    last four. Then N joins tier 2 and T becomes `retiring` for `waiting_days`.
  - **`denied`:** a clear "no". It raises an alert that sets D9's fact for every member.
  - **`not_confirmed`:** anything else. Nothing changes.
  - The portal shows the requester only "received" or "closed", never the outcome or why.
  - Storage, routes, guard and caps follow 0042's pattern, in `voice.profile_calls`. Confirmation
    calls belong to the vendor's demo dataset, so `demo reset` clears their caps.
  - Vendor users live in `profile.vendor_users`. The sign-up trigger skips them. Restrictive
    row-level security covers `app`, `ledger`, `voice`, `audit` and `profile`, and every customer
    endpoint answers 403 by an explicit check.
- **D15. Evidence.**
  - Profile events go in an append-only `profile.events` table, hash-chained per profile with the
    same canonical JSON and SHA-256 as `audit` (reusing `audit.jcs`). Integers and strings only.
  - Events: `profile_created`, `member_linked`, `member_unlinked`, `anchored`, `anchor_disagrees`,
    `onboarding_number_unknown`, `number_flagged`, `number_restored`, plus phase 2's request events.
  - A new job, `verify_profile_chains`, checks them. A broken chain raises an alert on the profile
    and shows on its panel.
  - Case evidence changes in one place: `CallEndedBody` gains an optional `not_vendor`.
- **D16. What each side sees.**
  - The customer's reviewer sees the profile panel:
    - the ordered list: last four digits, tier, when listed, and the month each number was last
      reached by a call;
    - this case's tries;
    - the vendor's legal-name history;
    - its bank accounts as last four digits, with first and last seen;
    - this customer's own documents, through the existing lineage page.
  - A reviewer never sees another customer's name, documents, answers, rule codes or individual
    calls.
  - Until a tenant model exists, row-level security does not enforce this between customers. Only
    the pages do (Out of scope).
- **D17. The flag.** `DIALBACK_PROFILE` is `off` or `on`, safe default `off`, read in
  `apps/core/src/dialback_core/flags.py`. With `off`, nothing is linked, read or written, and
  selection, calling and the state machine behave exactly as today. The new guards never fire,
  because no selection is a profile pick.

## What a profile holds

| Group | Contents | Shared with every member customer? |
|---|---|---|
| Identity | Profile ID, scope, tax-ID fingerprint (never the tax ID), legal name and its history | Yes |
| Members | Which customers' vendor records are linked, and when | No; each customer sees only its own link |
| Phone list | Each number: E.164, role, tier, `listed_at`, state (listed, flagged, retiring, retired), the month it was last reached | Yes, as last four digits on customer pages |
| Bank accounts | Fingerprint, routing, last four, bank name, first and last seen, current or previous | Last four and dates only |
| Documents | Emails, invoices, contracts, W-9s | Never; each stays in its customer's ledger |
| Alerts | Unknown onboarding numbers, a disagreeing anchor, a name mismatch, a broken chain | The alert, without naming the customer that caused it |
| Events | The hash-chained profile log | Ramp operators; customers see the facts above |

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:** `shared_profile` in `SourceType`, and the optional `profile`
  object (D13). `profile_unavailable` is a value of the free-text top-level `reason`, so the schema
  needs no change for it.
- **`schemas/evidence-event.schema.json`:** an optional `not_vendor` boolean on `CallEndedBody`.
- **`openapi/dialback.yaml`:**
  - `GET /datasets/{datasetId}/cases/{caseId}/profile` (the panel) and `POST
    .../profile/numbers/{e164}/restore` (approvers only);
  - in phase 2, `GET /vendor/me`, `GET /vendor/profile`, `POST /vendor/requests` and `GET
    /vendor/requests`;
  - regenerated types.
- **`openapi/internal.yaml`, phase 2:** `POST /profile-calls` and `POST
  /internal/profile-calls/{id}/ended`.
- **The ledger enum `ledger.source_type` gains `shared_profile`.** This is a ledger migration,
  owned by Inesh, in the same PR.

Not contracts:

- `profile.events` has its own table and schema.
- The mock's `tax_id_fingerprint` lives in the mock's own model (Q87).

## Acceptance

Linking, onboarding and the list

- [ ] **L1.** The mock returns `tax_id_fingerprint` for every vendor that has a tax ID. Equal
  normalized tax IDs give equal fingerprints across businesses. The tax ID is never returned.
- [ ] **L2.** Vendors with the same fingerprint, scope and normalized legal name are members of one
  profile. A name mismatch links nothing and raises an alert. Lab datasets never link. Demo and
  live profiles never share members. Retiring a dataset unlinks its vendors.
- [ ] **L3.** The anchor rule without the tax-ID check lists the anchor in tier 1 for a vendor whose
  fixtures pass. `no_anchor` lists nothing. A later run that disagrees raises an alert and keeps the
  old anchor.
- [ ] **L4.** An onboarding contact number that isn't on the profile's list raises
  `onboarding_number_unknown` and is never listed.
- [ ] **L5.** The list is ordered by tier, then `listed_at`, then E.164. It holds only numbers
  listed before the cutoff, never a flagged one, and each number only once.
- [ ] **L6.** Every profile event is in the profile's chain, and `verify_profile_chains` raises an
  alert when one is edited.

Bank-change calls

- [ ] **K1.** With `DIALBACK_PROFILE=off`, every existing test passes unchanged. The one exception
  is the engine-version assertion in `packages/decision/tests/test_engine.py`, which moves to
  `decision-1.1.0` with NR22. That test is not wrong; the version changes on purpose.
- [ ] **K2.** With `on`, try 1 calls the first listed number. After a didn't-connect result, the
  next try calls the next listed number, then wraps to the top. Each try writes its own selection
  and `number_resolved`, with try-numbered keys. V01 holds for the number actually dialed.
- [ ] **K3.** After `failed` or `callback_requested`, the next try redials the same number.
- [ ] **K4.** A call that reaches someone naming an unrelated company flags that number for every
  member (tiers 1 and 2) or for this customer (tier 3), goes to `retry_wait`, and the next try calls
  the next number. A partial company name is decided as today and is never flagged.
- [ ] **K5.** A call that reaches the vendor is decided as today. A "no" or wrong digits never
  moves on to another number.
- [ ] **K6.** When the 3 tries run out, the case is Needs review, and the panel lists each try with
  its number's last four and its result.
- [ ] **K7.** A shared number is dialed even when this customer's ledger excludes it, and the voice
  service dials the selection named by the latest `number_resolved`, not the newest row.
- [ ] **K8.** A customer with no history for the vendor gets a call to the shared list. With no
  change request recorded, the outcome is Needs review, never Verified.
- [ ] **K9.** If the profile can't be read when a number is selected, nothing is picked. The
  selection is `no_provable_number` with reason `profile_unavailable`, and a person decides (R7).

The decision

- [ ] **N1.** `NR22_profile_changed_recently` fires when any D9 event has an `effective_at` between
  7 days before the cutoff and the moment the number is selected, even if every V rule holds. The
  fact is unknown off a profile, and NR22 doesn't fire then. An unreadable fact on a profile counts
  as true.

The dashboard

- [ ] **W1.** A case on a profile shows the profile panel (D16). No other customer's name,
  documents, answers, rule codes or individual calls appear on any page.
- [ ] **W2.** An approver can restore a flagged number. A clerk can't.

The demo

- [ ] **M1.** `dialback-core demo network [--phone acme=+1...] [--phone2 acme=+1...]` seeds one
  demo group of four fictional customers that pay Acme:
  - Fictional Co;
  - Harbor Supply Co;
  - Pinecrest Clinic, with no Acme documents;
  - a sham, Quickstart Holdings, whose "Acme Fasteners LLC" has Acme's tax ID and an onboarding
    contact of +1 206 555 0199.

  It links them and runs the anchor. Acme's fixtures name the team phone as the anchor, swapped in
  at run time and never committed. It backdates the links and the anchor to the seeded dates,
  except the sham's link, which is today. `demo change` and `demo status` take `--dataset <ref>`,
  and `demo reset` retires the whole group.
- [ ] **M2.** After `demo network`, Acme's profile has 4 members and the anchor in tier 1. The
  sham's number is not listed, and `onboarding_number_unknown` shows on the panel.
- [ ] **M3.** A bank change at Pinecrest calls the anchor (the team phone) and can end Verified,
  given a recorded change request. Today it would get no call.
- [ ] **M4.** With `--phone2`, Harbor Supply's own proven number is a second team phone. A bank
  change at Harbor whose first call is left unanswered calls the second phone on try 2.

Phase 2 (only after the items above pass)

- [ ] **V1.** A vendor user sees its profile and can ask to replace a trusted number. It sees
  "received" or "closed", never the outcome. It gets 403 from customer endpoints and reads nothing
  through row-level security, `audit` and `profile` included.
- [ ] **V2.** The confirmation call runs D14's script. `confirmed` needs all four conditions. A
  "no" raises the alert that sets NR22's fact for every member. Anything else changes nothing.
- [ ] **V3.** Typed digits are never stored or logged. The guard refuses anything it can't read.
  A call result applies once, and only to a pending request.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| L1 | `apps/ramp-mock/tests/test_tax_fingerprint.py` |
| L2 to L6 | `packages/profile/tests/test_link.py`, `test_anchor.py`, `test_onboarding_numbers.py`, `test_list.py`, `test_chain.py` |
| K1 | the existing suites with the flag `off` |
| K2 to K9 | `apps/core/tests/test_profile_calling.py` (one test per item), `packages/profile/tests/test_classify.py` (D8), the state-machine cases in `scripts/db/test.sh`, `apps/voice/tests/test_named_selection.py` |
| N1 | `packages/decision/tests/test_rules.py::test_nr22_window`, `::test_nr22_unknown_off_profile`, `::test_nr22_unreadable_counts_true` |
| W1, W2 | `apps/core/tests/test_profile_panel.py`, plus `pnpm typecheck`, `pnpm build` and screenshots |
| M1 to M4 | `apps/core/tests/test_demo_network.py`, and the network beats in `scripts/dev/demo_rehearsal.py` |
| V1 to V3 | `apps/core/tests/test_vendor_portal.py`, `packages/policy/tests/test_profile_script.py`, `apps/voice/tests/test_profile_calls.py`, and the vendor row-level security cases in `scripts/db/test.sh` |

## Build plan

Phase 1 first. Each PR stays under about 400 lines.

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 0 | 0041's anchor code from `sami/onboarding-anchors`, without the tax-ID check; `state` filled in the demo seed | Sami (partner Inesh) | | L3 |
| 1 | Contracts, plus the ledger enum value | all owners; enum Inesh | | |
| 2 | `supabase/migrations/profile/0001_profile.sql` creates the schema (profiles, members, numbers, alerts, events, row-level security); `profile` is added to the order in `scripts/db/apply.sh` and `scripts/db/migrate.py` after `ledger`; `packages/profile` (link, anchor, onboarding check, list, classify, flag and restore, chain), added to the workspace, `py.yml` and the images | Sami | 0, 1 | L2 to L6 |
| 3 | Mock fingerprint; `tin_hash` and `list_vendor_contacts` on sync; tax IDs and states in the demo seed | Abhi | 2 | L1, L4 |
| 4a | The state machine (D12), planned in plan mode, regenerating `docs/state-machine.md` | Abhi | 1 | K2 to K6 (SQL) |
| 4b | The profile resolver behind the flag; try-numbered keys; classifying before `call_ended`; the decide handler's port | Abhi | 2, 3, 4a | K2 to K9 |
| 4c | The voice service dialing the named selection, and the status check by tier | Sami | 1 | K7 |
| 4d | NR22, its fact and the engine bump | Abhi, partner Inesh | 1 | N1, K1 |
| 5 | The profile panel and the restore action | Abhi (core), web owners | 2, 4b | W1, W2 |
| 6 | `demo network`, the demo group, backdating, `--dataset` | Sami | 2 to 5 | M1 to M4 |
| 7 | Phase 2: portal, vendor users, confirmation call | Sami, Abhi, web owners | 6 | V1 to V3 |

## Out of scope

- A new vendor's first appearance opening a case and holding its first payment (spec 0002 Q2).
- Real registry and domain lookups; the anchor runs on fixtures.
- Bank details in the portal.
- A notice to the old number.
- Account recovery.
- Mailbox intake.
- ADR 0015's warning signals.
- Promoting a customer's proven number into the shared list.
- A tenant model for customers. Pages enforce D16, but row-level security doesn't yet, and demo
  sign-in accounts see every dataset in a demo group.
- Real Ramp's fingerprint (Q87).
- A network-wide call cap per number (Q90).
- Turning a pre-dial refusal (for example, a number not on the allowlist) into a move to the next
  number. It stays `command_failed`, which goes to review, as today.

## Open questions

- **Q87. Real Ramp.** Can Ramp return a network tax-ID fingerprint, or compute one for Dialback?
  Until it can, `sandbox` never links.
- **Q88. Who the phase 2 confirmation call is for.** No single customer applies, and the speech
  guard blocks "verif" words. Proposed disclosure: "calling for the accounts payable teams of your
  customers on Ramp", plus a callback number.
- **Q89. The author's calls, to confirm:**
  - D11: with no recorded request, the case still calls, then a person decides;
  - `waiting_days = 7`;
  - `next_number_delay_seconds = 120`;
  - a flagged tier 1 or 2 number leaves every member's list;
  - 3 tries per case, so a list longer than 3 is never fully walked.
- **Q90. A network-wide cap per number.** Today's cap counts per contact point, which means per
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
- **Q95. A real opt-out store.** `ledger.contact_points.status` is only `eligible` or `excluded`, and
  NR11's opt-out flag is never saved per number. A person who says "don't call this number" on any
  call should be saved, and honored for every pick.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
scripts/db/test.sh
cd packages/onboarding && uv run pytest
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
