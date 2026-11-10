# 0045: Shared vendor profile, an ordered list of trusted numbers, and the vendor portal

**Owner:** Sami  **Slice:** call and adversary, with money-path changes listed under Build plan
**Target day:** Nov 10, 2026. Sami set this on Nov 9, overriding the freeze dates in AGENTS.md.
**Builds on:** 0021 (the selection rule), 0042 (the pattern for a second kind of call). **Changes:**
ADR 0015 (PR #92) deferred shared trust; this spec adopts it for phone numbers, under the rules
below.

## Goal

Today each customer proves a vendor's phone number on its own, from 90 days of its own documents
(spec 0021). A customer new to a vendor gets no call, and a case redials one number until it gives
up. When this spec is done:

- **One shared profile per real vendor** links that vendor's records at every customer, by a tax-ID
  fingerprint that Ramp computes (the mock plays Ramp) and the vendor's legal name.
- **Each case gets an ordered list:** this customer's own proven number first, then the profile's
  shared numbers, oldest first. The shared numbers are the vendor's onboarding numbers and, in phase
  2, numbers confirmed by a call to a trusted number.
- **The case calls down the list by itself.** It moves on when a call doesn't connect or reaches
  someone who isn't the vendor, and stops as soon as it reaches the vendor. When the tries run out,
  a person decides.
- **A customer that already knows the vendor is exactly as safe as today.** Its own proven number
  is called first. The shared list adds a call for a customer with no history, and a fallback.
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

## Assumptions and the known limit

- **A1. Onboarding is checked elsewhere, for now.** The numbers a customer's Ramp record had when
  Dialback first saw the vendor are assumed valid, checked by Ramp's onboarding. Dialback builds no
  onboarding check in this spec. Each such number records `via = onboarding_assumed`, so a later
  onboarding check can re-check every one of them, including those listed in the meantime.
- **A2. The known limit.** If onboarding is fooled, a wrong number joins the shared list. For
  example, a sham business on Ramp adds "Acme Fasteners LLC" with Acme's tax ID and its own phone
  number. Four guards narrow what it can do:
  1. A customer's own proven number is called first (D6). The sham's number can reach only a
     customer with no history for the vendor, or a case whose own number didn't answer.
  2. Shared numbers are called oldest first (D6). The real vendor's numbers, listed long before,
     rank ahead of the sham's.
  3. For 90 days after it is listed, a shared number that is dialed can only lead to Needs review
     (D9).
  4. One customer can't remove or restore a shared number for everyone (D8).

  What remains: after 90 days, a customer with no history whose call to the real vendor's numbers
  isn't answered could reach the sham's number. Today that customer gets no call at all.

## Decisions

### The profile

- **D1. Linking.**
  - The Ramp mock computes `tax_id_fingerprint = HMAC-SHA256(DIALBACK_NETWORK_KEY, normalized tax
    ID)` and returns it on the vendor representation. It never returns the tax ID itself.
  - The core stores the fingerprint in `app.vendors.tin_hash` on sync.
  - A profile is keyed by (fingerprint, scope, normalized legal name). Legal suffixes such as LLC
    and Inc, punctuation and case are ignored. The scope is `live` for ordinary datasets, or the
    demo group's ID for demo datasets. So demo and real profiles never mix, and a member with a
    different name gets a separate profile instead of locking anyone out. Two profiles with the same
    fingerprint and scope but different names raise an alert for a person.
  - Lab datasets never link. Retiring a dataset unlinks its vendors.
  - A change of `tin_hash` or of the legal name on a vendor unlinks it and links it again, which
    dates a new link (D9).
  - With `DIALBACK_RAMP=sandbox`, no fingerprint arrives and nothing links (Q87).
- **D2. Onboarding numbers (shared).**
  - The first time the core syncs a vendor that belongs to a profile, it reads the vendor's Ramp
    contacts through a new `RampPort.list_vendor_contacts` (the endpoint `apps/core/README.md`
    already plans).
  - The phone numbers in that first read are the member's onboarding numbers. They are listed on
    the profile with `via = onboarding_assumed` and `listed_at` set to that read.
  - Contacts added or edited later are never listed. A contact change is the usual first step of a
    takeover.
  - The read is stored as numbers only, never names or emails.
- **D3. Numbers confirmed by a call (shared).** Phase 2 only (D14).
- **D4. This customer's own proven number.** This is the ledger's own pick under spec 0021,
  unchanged. It is never shared.
- **D5. Which shared numbers a case may use.**
  - A shared number must be listed before the cutoff, and must not be flagged for this customer or
    for the network (D8). The cutoff is the ledger selection's own `cutoff`: the earliest of the
    change in Ramp, the recorded change request and the first message from a lookalike domain, as
    in spec 0021.
  - If this customer's ledger excluded the same number for one of these reasons, it stays usable:
    `too_few_source_types`, `younger_than_minimum_age`, `first_seen_in_change_context`. These are
    "not enough history here", which the shared list makes up for.
  - Any other ledger exclusion drops it from this case's list: `near_miss_of_older_number`,
    `not_vendor_party`, `fax_line` or `hidden_text_only`.

### Calling down the list

- **D6. The order.**
  1. This customer's own proven number, if the ledger has one.
  2. The usable shared numbers, oldest `listed_at` first, then E.164 order. Each number appears
     only once.
- **D7. Which number each try calls.** At most 3 tries per case, the existing cap.
  - Try 1 calls the first number on the list.
  - A try after a call that **didn't connect** calls the next number not yet dialed in this case
    version. When the list runs out, it goes back to the top. Didn't connect means the result was
    `no_answer`, `busy`, `voicemail`, `machine` or `declined`, where `declined` is a hang-up before
    the consent answer.
  - A try after a call that **reached someone who isn't the vendor** (D8) also calls the next
    number.
  - A try after `failed` or `callback_requested` redials the **same** number, as today. A `failed`
    call can end after consent, with a "no" already said.
  - A call that **reached the vendor** is decided as today. A "no", wrong digits or a partial
    company name never moves on to another number.
  - When the tries run out, the case goes to Needs review.
  - A case with no own proven number and no usable shared number is `no_provable_number`, as today.
- **D8. "Not the vendor," and flags.**
  - Code in `packages/profile` classifies a finished `answers_recorded` call as not the vendor when
    consent was given, a company was named, and the named company shares no significant word with
    any name of this profile's members or the profile's name history. Suffixes like LLC, Inc and
    Co, and common words like "the" and "company", don't count.
  - The core classifies before applying `call_ended`.
  - A not-the-vendor result **flags the number for this customer only**.
  - A shared number is **flagged for the network** only when not-the-vendor results from two
    different customers' cases agree.
  - This customer's approver can restore its own flag. Only a Ramp operator can restore a network
    flag (a command, with no page in phase 1).
  - Flags and restores are chained.
  - A not-the-vendor call is left out of the earlier-call evidence `decide()` reads, so a stranger's
    "no" never blocks the real vendor later.
- **D9. When a shared number may only lead to review.** A new tri-state fact,
  `shared_number_needs_review`, applies when the dialed number is a shared number (not this
  customer's own). It is true when any of these holds:
  - the number was listed less than 90 days before the cutoff;
  - within the 7 days before the moment of the decision, a number on this case's list was flagged
    or restored, or this case's own vendor was linked or relinked;
  - this case's own vendor was relinked with a changed legal name less than 90 days before the
    cutoff.

  The fact is unknown when the dialed number is this customer's own, or the case isn't on a profile.
  The decide handler works out whether a case is on a profile from the `number_resolved` event's
  `rule_version`, so a failed read can't make it look off a profile. If the fact can't be computed
  for a profile pick, it is treated as true.

  It feeds a new rule, `NR22_shared_number_needs_review`. The engine version becomes
  `decision-1.1.0`.

  Times come from each event's `effective_at`. Only `demo network` may backdate it, which a SQL
  check enforces for demo datasets only.
- **D10. No change request on record.** The call still happens. V07 can't hold without the request,
  so the outcome is Needs review, with the call's answers attached. The decision engine is
  unchanged (Q89).

### How it plugs in

- **D11. The case state machine** (redefine `app.eval_guard` and `app.apply_case_event` the way
  0008 and 0011 did, and regenerate `docs/state-machine.md`):
  - **Profile cases on a retry ask for a new number.** On a case whose selection is a profile pick,
    `retry_timer_fired` stays in `retry_wait` and enqueues `select_number` keyed
    `{case}:select:v{N}:a{attempt}`. Try 1 keeps today's key `{case}:select:v{N}`. Other cases are
    unchanged and enqueue `place_call` directly.
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
- **D12. How a pick reaches the voice service.**
  - **When the list starts with this customer's own number,** try 1 uses the ledger's own selection
    row, exactly as today.
  - **Every other try's pick is a `ledger.number_selections` row** with:
    - `rule_version = profile-select-v1`;
    - source type `shared_profile`, or the ledger's own source types for the customer's own number;
    - `cutoff` and `requested_cutoff` taken from the ledger's selection;
    - `rule_settings` set to the ledger's settings;
    - `candidates` set to the ordered list as **last four digits only** (rank = position);
    - a `profile` block: tier, try number, how many numbers were already dialed, and the hash of
      the list snapshot. It carries no profile ID and no full shared numbers, because the dashboard
      can fetch this document;
    - `inputs_sha256` taken over the snapshot.
  - **The full snapshot** (the ordered list with full numbers and states) is stored server-side in
    `profile.selection_snapshots`, keyed by its hash, for replay.
  - **A shared pick's contact point** upserts the ledger's own row. It uses the ledger's ID
    (`uuid5(vendor_id:e164)`), `status = eligible`, `first_seen_at = listed_at` and source types
    `{shared_profile}`, and never changes an existing row.
  - **The voice service dials the selection named by the case's latest `number_resolved`** in the
    current version, read from `audit.evidence_events` the way `apps/core/.../api.py` already
    resolves it. It does not dial the newest row. It keeps the ledger status check for the
    customer's own number, and skips it for a shared pick, which D5 has already filtered. No real
    opt-out store exists yet (Q95).
  - **V01's ID and text stay as they are.**
  - **The decide handler reads the profile block and computes D9's fact through a port,** as
    `LedgerChangeContext` does, and records the fact in the decision. `decide()` stays pure.
- **D13. Integrity.**
  - Profile events go in an append-only `profile.events` table, hash-chained per profile with the
    same canonical JSON and SHA-256 as `audit` (reusing `audit.jcs`). Integers and strings only.
  - The list a selection reads is rebuilt from the events, or the selection verifies the chain head
    first. A broken chain, a partial read or any error gives `no_provable_number` with reason
    `profile_unavailable`, and a person decides (R7).
  - A job, `verify_profile_chains`, also checks every chain and raises an alert.
  - Events: `profile_created`, `member_linked`, `member_unlinked`, `number_listed`,
    `number_flagged`, `number_restored`, `name_conflict`, plus phase 2's request events.
  - The `profile` schema grants nothing to signed-in users. Only the service reads and writes it.
- **D14. Phase 2: the vendor portal and the confirmation call.**
  - A vendor user asks to replace trusted number T with new number N.
  - The voice service calls the profile's oldest usable shared number, with its own script:
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
- **D15. What each side sees.**
  - The customer's reviewer sees the profile panel:
    - this case's ordered list: last four digits, own or shared, and the month each was listed;
    - this case's tries and results;
    - the vendor's name history;
    - this customer's own documents, accounts and earlier calls, through the existing pages.
  - A reviewer never sees another customer's name, accounts, documents, answers or calls, and never
    a full shared number.
  - Until a tenant model exists, row-level security on the `app` and `ledger` schemas doesn't
    separate customers. Only the pages and the `profile` schema's grants do (Out of scope).
- **D16. The flag.** `DIALBACK_PROFILE` is `off` or `on`, safe default `off`, read in
  `apps/core/src/dialback_core/flags.py`. With `off`, nothing is linked, read or written, and
  selection, calling and the state machine behave exactly as today. The new guards never fire,
  because no selection is a profile pick.

## What a profile holds

| Group | Contents | Visible to a member customer? |
|---|---|---|
| Identity | Profile ID, scope, tax-ID fingerprint (never the tax ID), legal name and its history | Name history only |
| Members | Which customers' vendor records are linked, and when | No |
| Shared numbers | E.164, `via`, `listed_at`, network flag, which member listed it | Last four and listing month, only as part of a case's list |
| Flags | Per-customer and network flags, with the calls behind them | Only its own flags |
| Snapshots | The full list behind each profile selection | No; server-side, for replay |
| Events | The hash-chained profile log | No; Ramp operators |

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:** `shared_profile` in `SourceType`, and the optional `profile`
  object (D12). `profile_unavailable` is a value of the free-text top-level `reason`, so it needs no
  schema change.
- **`schemas/evidence-event.schema.json`:** an optional `not_vendor` boolean on `CallEndedBody`.
- **`openapi/dialback.yaml`:**
  - `GET /datasets/{datasetId}/cases/{caseId}/profile` (the panel) and `POST
    .../profile/flags/{lastFour}/restore` (this customer's approvers, own flags only);
  - in phase 2, `GET /vendor/me`, `GET /vendor/profile`, `POST /vendor/requests` and `GET
    /vendor/requests`;
  - regenerated types.
- **`openapi/internal.yaml`, phase 2:** `POST /profile-calls` and `POST
  /internal/profile-calls/{id}/ended`.
- **The ledger enum `ledger.source_type` gains `shared_profile`.** This is a ledger migration,
  owned by Inesh, in the same PR.

## Acceptance

Linking and the list

- [ ] **L1.** The mock returns `tax_id_fingerprint` for every vendor that has a tax ID. Equal
  normalized tax IDs give equal fingerprints across businesses. The tax ID is never returned.
- [ ] **L2.** Vendors with the same fingerprint, scope and normalized legal name are members of one
  profile. A different name makes a separate profile and raises `name_conflict`. Lab datasets
  never link. Demo and live never mix. Retiring a dataset unlinks its vendors. A tax-ID or name
  change relinks the vendor.
- [ ] **L3.** The phone numbers in a member's first contacts read are listed with `via =
  onboarding_assumed`. Numbers in contacts added or edited later never are.
- [ ] **L4.** The order is this customer's own proven number first, then shared numbers listed
  before the cutoff, oldest first, each once. It excludes flagged numbers and numbers the ledger
  excluded for a reason outside D5's three.
- [ ] **L5.** Every profile event is chained. Selection from a broken or partly read profile gives
  `profile_unavailable` and a person decides. `verify_profile_chains` alerts on an edited chain.
- [ ] **L6.** A signed-in user can read nothing in the `profile` schema directly.

Bank-change calls

- [ ] **K1.** With `DIALBACK_PROFILE=off`, every existing test passes unchanged. The one exception
  is the engine-version assertion in `packages/decision/tests/test_engine.py`, which moves to
  `decision-1.1.0` with NR22. That test is not wrong; the version changes on purpose.
- [ ] **K2.** With `on`, a customer that has its own proven number calls it first, through the
  ledger's own selection, exactly as today.
- [ ] **K3.** After a didn't-connect result, the next try calls the next number on the list, then
  wraps to the top. Each try writes its own selection and `number_resolved` with try-numbered keys.
  V01 holds for the number actually dialed.
- [ ] **K4.** After `failed` or `callback_requested`, the next try redials the same number.
- [ ] **K5.** A call that reaches someone naming an unrelated company flags the number for this
  customer, goes to `retry_wait`, and the next try calls the next number. A second such result at
  another customer flags it for the network. A partial company name is decided as today and is
  never flagged.
- [ ] **K6.** A call that reaches the vendor is decided as today. A "no" or wrong digits never
  moves on. A not-the-vendor call never appears in the earlier-call evidence.
- [ ] **K7.** A customer with no history for the vendor calls the shared list. With no change
  request recorded, the outcome is Needs review, never Verified.
- [ ] **K8.** The voice service dials the selection named by the latest `number_resolved`, not the
  newest row.
- [ ] **K9.** A selection document returned by the API carries last four digits only for shared
  numbers, and no profile ID.

The decision

- [ ] **N1.** `NR22_shared_number_needs_review` fires for a shared pick when any D9 condition
  holds, even if every V rule holds. It never fires when the dialed number is the customer's own.
  An uncomputable fact on a profile pick counts as true. Backdating `effective_at` outside a demo
  dataset is refused by the database.

The dashboard

- [ ] **W1.** A case on a profile shows the panel (D15). No other customer's name, accounts,
  documents, answers or calls, and no full shared number, appear on any page.
- [ ] **W2.** An approver can restore its own customer's flag, not a network flag. A clerk can't
  restore anything.

The demo

- [ ] **M1.** `dialback-core demo network [--phone acme=+1...] [--phone2 acme=+1...]` seeds one
  demo group of four fictional customers that pay Acme:
  - Fictional Co and Harbor Supply Co, whose Acme contact is the team phone, listed two years ago
    (backdated);
  - Pinecrest Clinic, with no Acme history;
  - a sham, Quickstart Holdings, whose "Acme Fasteners LLC" has Acme's tax ID and a contact of +1
    206 555 0199, listed today.

  `--phone2` makes Harbor's own proven number a second team phone. Team phones are typed at run
  time and never committed. `demo change` and `demo status` take `--dataset <ref>`, and `demo reset`
  retires the whole group.
- [ ] **M2.** For Pinecrest, Acme's list is the team phone, then the sham's number.
- [ ] **M3.** A bank change at Pinecrest calls the team phone and, with a recorded change request,
  can end Verified. Today it would get no call.
- [ ] **M4.** At Harbor (with `--phone2`), the first try calls Harbor's own number. If it's left
  unanswered, the second try calls the shared team phone.
- [ ] **M5.** If the team phone is left unanswered at Pinecrest, the next try reaches the sham's
  number, and the case can only end in Needs review (NR22). This is the known limit A2 working as
  designed.

Phase 2 (only after the items above pass)

- [ ] **V1.** A vendor user sees its profile and can ask to replace a trusted number. It sees
  "received" or "closed", never the outcome. It gets 403 from customer endpoints and reads nothing
  through row-level security.
- [ ] **V2.** The confirmation call runs D14's script. `confirmed` needs all four conditions. A "no"
  raises an alert. Anything else changes nothing.
- [ ] **V3.** Typed digits are never stored or logged. The guard refuses anything it can't read. A
  call result applies once, and only to a pending request.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| L1 | `apps/ramp-mock/tests/test_tax_fingerprint.py` |
| L2 to L6 | `packages/profile/tests/test_link.py`, `test_onboarding_numbers.py`, `test_list.py`, `test_chain.py`, and the `profile` grant cases in `scripts/db/test.sh` |
| K1 | the existing suites with the flag `off` |
| K2 to K9 | `apps/core/tests/test_profile_calling.py` (one test per item), `packages/profile/tests/test_classify.py` (D8), the state-machine cases in `scripts/db/test.sh`, `apps/voice/tests/test_named_selection.py` |
| N1 | `packages/decision/tests/test_rules.py::test_nr22_conditions`, `::test_nr22_never_on_own_number`, `::test_nr22_uncomputable_counts_true`, and a backdating case in `scripts/db/test.sh` |
| W1, W2 | `apps/core/tests/test_profile_panel.py`, plus `pnpm typecheck`, `pnpm build` and screenshots |
| M1 to M5 | `apps/core/tests/test_demo_network.py`, and the network beats in `scripts/dev/demo_rehearsal.py` |
| V1 to V3 | `apps/core/tests/test_vendor_portal.py`, `packages/policy/tests/test_profile_script.py`, `apps/voice/tests/test_profile_calls.py`, and the vendor row-level security cases in `scripts/db/test.sh` |

## Build plan

Phase 1 first. Each PR stays under about 400 lines.

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 1 | Contracts, plus the ledger enum value | all owners; enum Inesh | | |
| 2 | `supabase/migrations/profile/0001_profile.sql` creates the schema (profiles, members, numbers, flags, snapshots, events, no grants to signed-in users, the backdating check); `profile` is added to the order in `scripts/db/apply.sh` and `scripts/db/migrate.py` after `ledger`; `packages/profile` (link, onboarding numbers, list, classify, flags, chain, snapshot), added to the workspace, `py.yml` and the images | Sami | 1 | L2 to L6 |
| 3 | Mock fingerprint; `tin_hash` and `list_vendor_contacts` on sync; tax IDs in the demo seed | Abhi | 2 | L1, L3 |
| 4a | The state machine (D11), planned in plan mode, regenerating `docs/state-machine.md` | Abhi | 1 | K3 to K6 (SQL) |
| 4b | The profile resolver behind the flag; try-numbered keys; classifying before `call_ended`; leaving not-the-vendor calls out of the earlier-call evidence; the decide handler's port | Abhi | 2, 3, 4a | K2 to K9 |
| 4c | The voice service dialing the named selection, and the status check by pick type | Sami | 1 | K8 |
| 4d | NR22, its fact and the engine bump | Abhi, partner Inesh | 1 | N1, K1 |
| 5 | The profile panel and the restore action | Abhi (core), web owners | 2, 4b | W1, W2 |
| 6 | `demo network`, the demo group, backdating, `--dataset` | Sami | 2 to 5 | M1 to M5 |
| 7 | Phase 2: portal, vendor users, confirmation call | Sami, Abhi, web owners | 6 | V1 to V3 |

## Out of scope

- Onboarding checks: anchors and registry lookups (A1). Spec 0041's code stays in shadow mode. When
  it lands, it re-checks every `onboarding_assumed` number.
- A new vendor's first appearance opening a case and holding its first payment (spec 0002 Q2).
- Bank details in the portal.
- A notice to the old number.
- Account recovery.
- Mailbox intake.
- ADR 0015's warning signals.
- Promoting a customer's proven number into the shared list.
- A tenant model for customers (D15).
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
  - D10: with no recorded request, the case still calls, then a person decides;
  - the 90-day probation and the 7-day change window;
  - `next_number_delay_seconds = 120`;
  - two customers' results to flag a shared number for the network;
  - 3 tries per case, so a list longer than 3 is never fully walked.
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
