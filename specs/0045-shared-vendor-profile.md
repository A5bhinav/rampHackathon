# 0045: Shared vendor profile, an ordered list of trusted numbers, and the vendor portal

**Owner:** Sami  **Slice:** call and adversary, with money-path changes listed under Build plan
**Target day:** Nov 10, 2026. Sami set this on Nov 9, overriding the freeze dates in AGENTS.md.
**Builds on:** 0021 (the selection rule), 0042 (the pattern for a second kind of call). **Changes:**
ADR 0015 (PR #92) deferred shared trust; this spec adopts it for phone numbers, under the rules
below.

## Goal

Today each customer proves a vendor's phone number on its own, from 90 days of its own documents
(spec 0021). A customer new to a vendor gets no call, so a person has to handle every one of its
bank changes. When this spec is done:

- **One shared profile per real vendor** links that vendor's records at every customer, by a tax-ID
  fingerprint that Ramp computes (the mock plays Ramp) and the vendor's legal name.
- **A customer that already knows the vendor works exactly as today.** It calls its own proven
  number, redials it, and never falls through to a shared number.
- **A customer new to the vendor calls the vendor's shared numbers,** the ones other customers
  entered, ranked by how many customers entered each. It calls down that list by itself and can end
  Verified with no person involved.
- **Phase 2, the vendor portal:** a vendor asks to replace a trusted number, and a keypad
  confirmation call to a trusted number makes the change.

Rules served:

- R1 and R5 are unchanged.
- R2: a shared number is listed before the change, so it cannot come from the change request.
- R3: code builds the list, classifies each call and decides.
- R4 and R8: both calls disclose first and run the speech guard.
- R6: every profile change is hash-chained.
- R7: anything unclear goes to a person.
- R9: no text from a document changes the list. A call can only flag a number, through D8's fixed
  rule.

## Assumptions and the known limits

- **A1. Onboarding is checked elsewhere, for now.** The numbers a customer's Ramp record held when
  Dialback first read it are assumed valid, checked by Ramp's onboarding. Dialback builds no
  onboarding check in this spec. Each such number records `via = onboarding_assumed`, so a later
  onboarding check can re-check every one of them, including those listed in the meantime.
- **A2. The known limit that follows from A1.** If onboarding is fooled, a wrong number joins the
  shared list. For example, a sham business on Ramp adds "Acme Fasteners LLC" with Acme's tax ID and
  its own phone number. These guards bound what that number can do:
  1. A customer with its own proven number never calls a shared number (D6).
  2. A customer never uses a shared number that only its own records listed (D5), so an insider
     can't vouch for a number at their own company.
  3. Shared numbers rank by how many different customers listed them (D6). The real vendor's
     number, entered by many customers, comes before a sham's, entered by one.
  4. For 90 days after a number is first listed, a call to it can only lead to Needs review (D9).
  5. A vendor record whose tax ID or name changed can't use shared numbers for 90 days (D5).
  6. A "no" through a shared number can't block a case (D10).

  What remains: a customer new to a vendor whose only other customer on Ramp is a sham, or whose
  calls to the better-ranked numbers don't connect, could reach the sham's number after its 90 days.
  Today that customer gets no call at all. Q96 offers a stricter option.
- **A3. What other customers can read.** Shared numbers are written as ledger contact points and
  selections, which every signed-in user can read today (row-level security is `using (true)`).
  They are the vendor's business numbers, not customer data. Customer data never enters the
  profile, and the `profile` schema grants nothing to signed-in users. Fully separating customers
  needs the tenant model (Out of scope).

## Decisions

### The profile

- **D1. Linking.**
  - The Ramp mock computes `tax_id_fingerprint = HMAC-SHA256(DIALBACK_NETWORK_KEY, normalized tax
    ID)` and returns it on the vendor representation. It never returns the tax ID itself.
  - The core stores the fingerprint in `app.vendors.tin_hash` on sync.
  - A profile is keyed by (fingerprint, scope, normalized legal name). Legal suffixes such as LLC
    and Inc, punctuation and case are ignored. The scope is `live` for ordinary datasets, or the
    demo group's ID for demo datasets, so demo and real profiles never mix. A member with a
    different name gets a separate profile instead of locking anyone out. Two profiles with the
    same fingerprint and scope but different names raise `name_conflict`.
  - Lab datasets never link. Retiring a dataset unlinks its vendors.
  - A change of `tin_hash` or legal name on a vendor record unlinks it and links it again
    (`member_relinked`, D5).
  - With `DIALBACK_RAMP=sandbox`, no fingerprint arrives and nothing links (Q87).
- **D2. Onboarding numbers (shared).**
  - The first time the core syncs a Ramp vendor record that belongs to a profile, it reads the
    record's Ramp contacts once, through a new `RampPort.list_vendor_contacts` (the endpoint
    `apps/core/README.md` already plans). It never reads them again for that record: not on a
    relink, not on a later sync.
  - The phone numbers in that read are the record's onboarding numbers, listed with `via =
    onboarding_assumed`. A contact whose `updated_at` is more than one day after its `created_at`
    was edited after onboarding, and is not listed.
  - Each listing records which member listed it and when (`listed_at`).
  - When a member unlinks, its listings are removed. A number stays shared while any other member
    still lists it.
  - Only the numbers are stored, never names or emails.
- **D3. Numbers confirmed by a call (shared).** Phase 2 only (D15).
- **D4. This customer's own proven number.** This is the ledger's own pick under spec 0021,
  unchanged. It is never shared.
- **D5. Which shared numbers a case may use.** Shared numbers are used **only when this customer's
  ledger has no proven number** for the vendor. Of those, a number is usable when all of these
  hold:
  - it is listed by at least one member that belongs to **another customer** (another dataset).
    Listings from this customer's own records don't count;
  - its first listing by another customer is before the cutoff. The cutoff is the ledger selection's
    own `cutoff`: the earliest of the change in Ramp, the recorded change request and the first
    message from a lookalike domain, as in spec 0021;
  - it is not flagged for this customer or for the network (D8);
  - this case's vendor record has not been relinked in the 90 days before the cutoff;
  - if this customer's ledger excluded the same number, the reason was one of
    `too_few_source_types`, `younger_than_minimum_age` or `first_seen_in_change_context`, meaning
    "not enough history here". Any other ledger exclusion (`near_miss_of_older_number`,
    `not_vendor_party`, `fax_line`, `hidden_text_only`) drops it.

### Calling

- **D6. The order.**
  - **A customer with its own proven number** has a list of one: that number. Its case calls it,
    redials it after a no-answer, and then goes to a person, exactly as today. It never calls a
    shared number.
  - **A customer without one** gets the usable shared numbers, ranked by:
    1. how many different customers list the number, most first;
    2. then the earliest `listed_at` by another customer;
    3. then a keyed hash of the number (`HMAC(server key, e164)`), so an attacker can't choose a
       number that wins a tie.
- **D7. Which number each try calls** (shared lists only). At most 3 tries per case, the existing
  cap.
  - Try 1 calls the first number.
  - A try after a call that **didn't connect** calls the next number not yet dialed in this case
    version, wrapping to the top when the list runs out. Didn't connect means `no_answer`, `busy`,
    `voicemail`, `machine`, or `declined` (a hang-up before the consent answer).
  - A try after a call that **reached someone who isn't the vendor** (D8) also calls the next
    number.
  - A try after `failed` or `callback_requested` redials the **same** number, as today.
  - A call that **reached the vendor** is decided (D10). A "no", wrong digits or a partial company
    name never moves on to another number.
  - When the tries run out, the case goes to Needs review.
  - With no usable shared number, the case is `no_provable_number`, as today.
- **D8. "Not the vendor," and flags** (shared picks only; a customer's own number is decided as
  today).
  - Code in `packages/profile` classifies a finished `answers_recorded` call as not the vendor
    only when all of these hold:
    - consent was given and a company was named;
    - the named company shares no significant word with any legal name or trade name of this
      profile's members, or with the profile's name history. Suffixes like LLC, Inc and Co, and
      common words like "the" and "company", don't count;
    - the person did **not** answer the change question with "no" or "unsure";
    - the person did **not** give digits.

    Anything else is decided as a call that reached the vendor.
  - The core classifies before applying `call_ended`.
  - A not-the-vendor result flags the number for this customer only.
  - A number is flagged for the network only when not-the-vendor results from two different
    customers' cases agree.
  - This customer's approver can restore its own flag. Only a Ramp operator can restore a network
    flag (a command, with no page in phase 1).
  - Flags and restores are chained.
  - A not-the-vendor call is left out of the earlier-call evidence `decide()` reads.
- **D9. When a shared number can only lead to review.** A new tri-state fact,
  `shared_number_needs_review`, applies only when the dialed number is shared. It is true when any
  of these holds:
  - the number was first listed by another customer less than 90 days before the cutoff;
  - within the 7 days before the decision, the number was flagged or restored for the network, or
    flagged or restored for this customer.

  It is unknown when the dialed number is this customer's own, or the case isn't on a profile.
  Whether a case is a profile case comes from the `number_resolved` event's `rule_version`. If the
  fact can't be computed for a shared pick, it counts as true.

  It feeds a new rule, `NR22_shared_number_needs_review`.

  Times come from each event's `effective_at`. Only `demo network` may backdate it, which a SQL
  check enforces for demo datasets only.
- **D10. A "no" through a shared number can't block.** When the dialed number is shared, the
  Not verified rules (NV01 to NV04) don't fire. A new rule, `NR23_shared_number_denied`, gives
  Needs review instead, with the denial or the mismatch shown to the person. The NV rules gain the
  condition "the dialed number is this customer's own, or the case isn't on a profile". The engine
  version becomes `decision-1.1.0`.
- **D11. No change request on record.** The call still happens. V07 can't hold without the request,
  so the outcome is Needs review, with the call's answers attached. The decision engine is
  unchanged (Q89).

### How it plugs in

- **D12. The case state machine** (redefine `app.eval_guard` and `app.apply_case_event` the way
  0008 and 0011 did, and regenerate `docs/state-machine.md`). It affects shared lists only:
  - **On a shared-list case, a retry asks for a new number.** `retry_timer_fired` stays in
    `retry_wait` and enqueues `select_number` keyed `{case}:select:v{N}:a{attempt}`. Try 1 keeps
    today's key `{case}:select:v{N}`. Every other case enqueues `place_call` directly, as today.
  - **New moves out of `retry_wait`:**
    - to `calling` on `number_resolved [number_selected]`, which enqueues `place_call`, as the move
      from `held` does;
    - to `needs_review` on `no_provable_number`.
  - **"Not the vendor" joins the retry path.** `call_ended [answers_recorded]` now also needs
    `not_vendor` false or absent. New guards `call_ended [not_vendor_tries_left]` go to
    `retry_wait`, and `[not_vendor_tries_exhausted]` go to `needs_review`.
  - **Delays.** A retry that changes number waits `next_number_delay_seconds` (default 120; demo
    datasets 30). A retry of the same number keeps today's delay.
  - **Event keys** include the try number, so a second selection in one version is never dropped as
    a duplicate.
- **D13. How a pick reaches the voice service.**
  - **A customer with its own proven number** uses the ledger's own selection row, exactly as today.
    The profile is not read.
  - **A shared pick** is a `ledger.number_selections` row with:
    - `rule_version = profile-select-v1`;
    - source type `shared_profile`;
    - `cutoff` and `requested_cutoff` taken from the ledger's stored selection;
    - `rule_settings` set to the ledger's settings;
    - `candidates` set to the ranked shared list;
    - a `profile` block: try number, how many numbers were already dialed, and the hash of the list
      snapshot;
    - `inputs_sha256` taken over the snapshot.
  - **The full snapshot** (the ranked list, with counts and states) is stored in
    `profile.selection_snapshots`, keyed by its hash, for replay.
  - **A shared pick's contact point** upserts the ledger's own row. It uses the ledger's ID
    (`uuid5(vendor_id:e164)`), `status = eligible`, `first_seen_at = listed_at` and source types
    `{shared_profile}`, and never changes an existing row.
  - **If the ledger returned no stored selection with a cutoff,** for example after an error, no
    shared number is used. The outcome is `no_provable_number` with reason `profile_unavailable`.
  - **`DIALBACK_PROFILE=on` requires `DIALBACK_RESOLVER=ledger`.** With any other resolver, the
    profile is treated as off and the core logs it.
  - **The voice service dials the selection named by the case's latest `number_resolved`** in the
    current version, read from `audit.evidence_events` the way `apps/core/.../api.py` already
    resolves it. It does not dial the newest row. It keeps the ledger status check for the
    customer's own number, and skips it for a shared pick, which D5 has already filtered. No real
    opt-out store exists yet (Q95).
  - **V01's ID and text stay as they are.** Its wording, "the number the Trust Ledger chose", is
    loose for a shared pick, which is still stored in the Trust Ledger's selection table.
  - **The decide handler reads the profile block and computes D9's fact through a port,** as
    `LedgerChangeContext` does, and records the fact in the decision. `decide()` stays pure.
- **D14. Integrity.**
  - Profile events go in an append-only `profile.events` table, hash-chained per profile with the
    same canonical JSON and SHA-256 as `audit` (reusing `audit.jcs`). Integers and strings only.
  - Every selection **rebuilds the list from the events**. A broken chain, a partial read or any
    error gives `profile_unavailable` and a person decides (R7). That can only happen on a
    shared-list case, because a customer with its own number never reads the profile.
  - A job, `verify_profile_chains`, also checks every chain and raises an alert.
  - Events: `profile_created`, `member_linked`, `member_unlinked`, `member_relinked`,
    `number_listed`, `listing_removed`, `number_flagged`, `number_restored`, `name_conflict`, plus
    phase 2's request events.
  - The `profile` schema grants nothing to signed-in users. Only the service reads and writes it.
- **D15. Phase 2: the vendor portal and the confirmation call.**
  - A vendor user asks to replace trusted number T with new number N.
  - The voice service calls the profile's best-ranked usable number with its own script:
    1. disclosure;
    2. consent;
    3. the company's name;
    4. "Did your company recently ask to change the phone number on its vendor profile?";
    5. "Please type the last four digits of the new number on your keypad, then press pound." Pound
       is required, to match spec 0043.
  - **`confirmed`** needs all four: consent, the company matches, a "yes", and the digits equal N's
    last four. Then N is listed with `via = confirmed_by_call` and T becomes `retiring` for 7 days.
  - **`denied`:** a clear "no". It raises an alert.
  - **`not_confirmed`:** anything else. Nothing changes.
  - The portal shows the requester only "received" or "closed".
  - Storage, routes, guard and caps follow 0042's pattern, in `voice.profile_calls`. Vendor users
    live in `profile.vendor_users`, are skipped by the sign-up trigger, and are fenced off by
    restrictive row-level security on every schema plus an explicit 403 check on customer
    endpoints.
  - Q94 records the residual risk.
- **D16. What the reviewer sees.** A new endpoint serves the profile panel as its own redacted
  document:
  - for each number on this case's list: area code and last four (for example "206 ... 0133"), how
    many customers list it, whether this customer lists it too, and the month it was first listed;
  - this case's tries and results, with any denial or mismatch shown in plain words;
  - the vendor's name history;
  - this customer's own documents, accounts and earlier calls, through the existing pages.

  It never shows another customer's name, accounts, documents, answers or calls. A restore is keyed
  by an opaque flag ID, not by digits.
- **D17. The flag.** `DIALBACK_PROFILE` is `off` or `on`, safe default `off`, read in
  `apps/core/src/dialback_core/flags.py`. With `off`, nothing is linked, read or written, and
  selection, calling, the state machine and the decision behave exactly as today. The new guards
  and rules never fire, because no selection is a shared pick.

## What a profile holds

| Group | Contents | Visible to a member customer? |
|---|---|---|
| Identity | Profile ID, scope, tax-ID fingerprint (never the tax ID), legal name and its history | Name history only |
| Members | Which customers' vendor records are linked, and when | No |
| Listings | Each number, which member listed it, `via`, `listed_at` | Through the panel: area code, last four, customer count, first-listed month |
| Flags | Per-customer and network flags, with the calls behind them | Only its own flags |
| Snapshots | The ranked list behind each shared selection | No; server-side, for replay |
| Events | The hash-chained profile log | No; Ramp operators |

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:** `shared_profile` in `SourceType`, and the optional `profile`
  object (D13). `profile_unavailable` is a value of the free-text top-level `reason`, so it needs
  no schema change.
- **`schemas/evidence-event.schema.json`:** an optional `not_vendor` boolean on `CallEndedBody`.
- **`openapi/dialback.yaml`:**
  - `GET /datasets/{datasetId}/cases/{caseId}/profile` (the panel) and `POST
    .../profile/flags/{flagId}/restore` (this customer's approvers, own flags only);
  - in phase 2, `GET /vendor/me`, `GET /vendor/profile`, `POST /vendor/requests` and `GET
    /vendor/requests`;
  - regenerated types.
- **`openapi/internal.yaml`, phase 2:** `POST /profile-calls` and `POST
  /internal/profile-calls/{id}/ended`.
- **The ledger enum `ledger.source_type` gains `shared_profile`.** This is a ledger migration,
  owned by Inesh, in the same PR.

## Acceptance

Linking and listing

- [ ] **L1.** The mock returns `tax_id_fingerprint` for every vendor that has a tax ID. Equal
  normalized tax IDs give equal fingerprints across businesses. The tax ID is never returned.
- [ ] **L2.** Vendors with the same fingerprint, scope and normalized legal name are members of one
  profile. A different name makes a separate profile and raises `name_conflict`. Lab datasets
  never link. Demo and live never mix. Retiring a dataset unlinks its vendors. A tax-ID or name
  change relinks the record.
- [ ] **L3.** A record's contacts are read once, ever. Their numbers are listed with `via =
  onboarding_assumed`, except contacts edited more than a day after they were created. A later
  sync or relink lists nothing new.
- [ ] **L4.** Unlinking a member removes its listings. A number listed by another member stays.
- [ ] **L5.** Every profile event is chained. Selection rebuilds the list from the events. A broken
  or partly read chain gives `profile_unavailable`. `verify_profile_chains` alerts on an edited
  chain.
- [ ] **L6.** A signed-in user can read nothing in the `profile` schema directly.

Calling

- [ ] **K1.** With `DIALBACK_PROFILE=off`, every existing test passes unchanged. The one exception is
  the engine-version assertion in `packages/decision/tests/test_engine.py`, which moves to
  `decision-1.1.0` with NR22 and NR23. That test is not wrong; the version changes on purpose.
- [ ] **K2.** With `on`, a customer with its own proven number calls only that number, redials it
  as today, and never reads the profile, even when it is unreadable.
- [ ] **K3.** A customer without one uses only shared numbers listed by another customer, first
  listed before the cutoff, not flagged, and not dropped by D5's ledger reasons. Its vendor record
  must not have been relinked in the last 90 days.
- [ ] **K4.** The ranking is customer count, then first listing by another customer, then the keyed
  hash. A number listed only by this customer's own records is never used.
- [ ] **K5.** After a didn't-connect result, the next try calls the next number, then wraps to the
  top. Each try writes its own selection and `number_resolved` with try-numbered keys. V01 holds
  for the number actually dialed.
- [ ] **K6.** After `failed` or `callback_requested`, the next try redials the same number.
- [ ] **K7.** A shared call naming an unrelated company, with no "no", no "unsure" and no digits,
  flags the number for this customer and moves on. A second such result at another customer flags
  it for the network. A call with a "no", an "unsure" or digits is never classified as not the
  vendor. A trade name counts as a match.
- [ ] **K8.** A "no" or wrong digits through a shared number gives Needs review
  (`NR23_shared_number_denied`), never Not verified, and never moves on.
- [ ] **K9.** With no change request recorded, a shared-list case ends in Needs review, never
  Verified.
- [ ] **K10.** The voice service dials the selection named by the latest `number_resolved`, not the
  newest row.
- [ ] **K11.** With no stored ledger selection, or with `DIALBACK_RESOLVER` other than `ledger`, no
  shared number is used.

The decision

- [ ] **N1.** `NR22_shared_number_needs_review` fires for a shared pick when a D9 condition holds,
  even if every V rule holds. It never fires when the dialed number is the customer's own. An
  uncomputable fact on a shared pick counts as true. Backdating `effective_at` outside a demo
  dataset is refused by the database.
- [ ] **N2.** A shared pick that passes every V rule, with none of D9's conditions, is Verified with
  no person involved before release.

The dashboard

- [ ] **W1.** A shared-list case shows the panel (D16). No other customer's name, accounts,
  documents, answers or calls appear on any page.
- [ ] **W2.** An approver can restore its own customer's flag by its ID, not a network flag. A
  clerk can't restore anything.

The demo

- [ ] **M1.** `dialback-core demo network [--phone acme=+1...]` seeds one demo group of four
  fictional customers that pay Acme:
  - Fictional Co and Harbor Supply Co, whose Acme contact is the team phone, listed two years ago
    (backdated);
  - Pinecrest Clinic, with no Acme history;
  - a sham, Quickstart Holdings, whose "Acme Fasteners LLC" has Acme's tax ID and a contact of +1
    206 555 0199, listed 100 days ago (backdated).

  The team phone is typed at run time and never committed. `demo change` and `demo status` take
  `--dataset <ref>`, and `demo reset` retires the whole group.
- [ ] **M2.** For Pinecrest, Acme's list is the team phone (2 customers), then the sham's number (1
  customer).
- [ ] **M3.** A bank change at Pinecrest calls the team phone and, with a recorded change request,
  ends Verified with no person until release. Today it would get no call.
- [ ] **M4.** A bank change at Harbor calls Harbor's own proven number only, as today, even when it
  goes unanswered.
- [ ] **M5.** If the team phone is left unanswered at Pinecrest, the next try reaches the sham's
  number. Saying "no" there gives Needs review (NR23), not a block. This is A2's known limit,
  working as designed.

Phase 2 (only after the items above pass)

- [ ] **V1.** A vendor user sees its profile and can ask to replace a trusted number. It sees
  "received" or "closed", never the outcome. It gets 403 from customer endpoints and reads nothing
  through row-level security.
- [ ] **V2.** The confirmation call runs D15's script. `confirmed` needs all four conditions. A "no"
  raises an alert. Anything else changes nothing.
- [ ] **V3.** Typed digits are never stored or logged. The guard refuses anything it can't read. A
  call result applies once, and only to a pending request.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| L1 | `apps/ramp-mock/tests/test_tax_fingerprint.py` |
| L2 to L6 | `packages/profile/tests/test_link.py`, `test_onboarding_numbers.py`, `test_listings.py`, `test_chain.py`, and the `profile` grant cases in `scripts/db/test.sh` |
| K1 | the existing suites with the flag `off` |
| K2 to K11 | `apps/core/tests/test_profile_calling.py` (one test per item), `packages/profile/tests/test_rank.py` and `test_classify.py`, the state-machine cases in `scripts/db/test.sh`, `apps/voice/tests/test_named_selection.py` |
| N1, N2, K8 | `packages/decision/tests/test_rules.py::test_nr22_conditions`, `::test_nr22_never_on_own_number`, `::test_nr23_shared_denial_is_review`, `::test_shared_pick_can_verify`, and a backdating case in `scripts/db/test.sh` |
| W1, W2 | `apps/core/tests/test_profile_panel.py`, plus `pnpm typecheck`, `pnpm build` and screenshots |
| M1 to M5 | `apps/core/tests/test_demo_network.py`, and the network beats in `scripts/dev/demo_rehearsal.py` |
| V1 to V3 | `apps/core/tests/test_vendor_portal.py`, `packages/policy/tests/test_profile_script.py`, `apps/voice/tests/test_profile_calls.py`, and the vendor row-level security cases in `scripts/db/test.sh` |

## Build plan

Phase 1 first. Each PR stays under about 400 lines.

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 1 | Contracts, plus the ledger enum value | all owners; enum Inesh | | |
| 2 | `supabase/migrations/profile/0001_profile.sql` creates the schema (profiles, members, listings, flags, snapshots, events, no grants to signed-in users, the backdating check); `profile` is added to the order in `scripts/db/apply.sh` and `scripts/db/migrate.py` after `ledger`; `packages/profile` (link, onboarding read, listings, usable numbers, rank, classify, flags, chain, snapshot), added to the workspace, `py.yml` and the images | Sami | 1 | L2 to L6, K3, K4 |
| 3 | Mock fingerprint; `tin_hash` and the once-only `list_vendor_contacts` on sync; tax IDs in the demo seed | Abhi | 2 | L1, L3 |
| 4a | The state machine (D12), planned in plan mode, regenerating `docs/state-machine.md` | Abhi | 1 | K5, K6, K7 (SQL) |
| 4b | The profile resolver behind the flag, used only without an own proven number; try-numbered keys; classifying before `call_ended`; leaving not-the-vendor calls out of the earlier-call evidence; the decide handler's port | Abhi | 2, 3, 4a | K2 to K11 |
| 4c | The voice service dialing the named selection, and the status check by pick type | Sami | 1 | K10 |
| 4d | NR22, NR23, the NV condition, the facts and the engine bump | Abhi, partner Inesh | 1 | N1, N2, K1, K8 |
| 5 | The profile panel and the restore action | Abhi (core), web owners | 2, 4b | W1, W2 |
| 6 | `demo network`, the demo group, backdating, `--dataset` | Sami | 2 to 5 | M1 to M5 |
| 7 | Phase 2: portal, vendor users, confirmation call | Sami, Abhi, web owners | 6 | V1 to V3 |

## Out of scope

- Onboarding checks: anchors and registry lookups (A1). When they land, they re-check every
  `onboarding_assumed` number.
- A new vendor's first appearance opening a case and holding its first payment (spec 0002 Q2).
- Shared numbers for customers that have their own proven number, including as a fallback.
- Bank details in the portal.
- A notice to the old number.
- Account recovery.
- Mailbox intake.
- ADR 0015's warning signals.
- Promoting a customer's proven number into the shared list.
- A tenant model for customers (A3).
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
  - the 90-day probation, the 90-day relink rule and the 7-day flag window;
  - `next_number_delay_seconds = 120`;
  - two customers' results to flag a shared number for the network;
  - 3 tries per case, so a list longer than 3 is never fully walked;
  - the one-day edit window in D2.
- **Q90. A network-wide cap per number.** Today's cap counts per contact point, which means per
  dataset. A real vendor called by every customer could get far more than 3 AI calls in 30 days. A
  network-wide cap would let a sham use up the real vendor's calls, so this needs a design of its
  own.
- **Q91. Ownership.** Proposed: `packages/profile` and the `profile` schema owned by Sami, with
  Inesh as partner.
- **Q92. The separate profile-combining work** mentioned on Nov 9 is not in the repo. This spec
  links by tax-ID fingerprint and legal name; reconcile before PR 2.
- **Q93. The network key in production:** a key-management service and rotation. In the demo it is
  an env var on the mock, and nothing in Dialback holds it.
- **Q94. Phase 2's residual risk.** An attacker who controls the vendor's email can prime the front
  desk to answer the confirmation call and type the digits. A longer wait for portal-confirmed
  numbers, or a second confirmation through another trusted number, would narrow this.
- **Q95. A real opt-out store.** `ledger.contact_points.status` is only `eligible` or `excluded`, and
  NR11's opt-out flag is never saved per number. A person who says "don't call this number" on any
  call should be saved, and honored for every pick.
- **Q96. A stricter option for A2.** Let a shared number produce Verified only when at least two
  different customers list it. A number listed by one other customer would still be called, but
  could only lead to Needs review. The fraudster would then need two fake businesses on Ramp.
- **Q97. Expiry.** Shared numbers never expire in phase 1. A vendor's old number could be reassigned
  years later, which widens FINDINGS A7. A last-confirmed date, or an expiry, needs a decision.

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
