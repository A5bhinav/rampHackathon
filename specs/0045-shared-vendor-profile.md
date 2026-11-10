# 0045: Shared vendor profile, trusted numbers and the vendor portal

**Owner:** Sami  **Slice:** call and adversary, with money-path changes listed under Build plan  **Target day:** Nov 10, 2026
**Builds on:** 0021 (the selection rule), 0041 (anchors; code on `sami/onboarding-anchors`), 0042 (the
pattern for a second kind of call). **Changes:** ADR 0015 (PR #92) deferred shared trust; this spec
adopts it for phone numbers, under the chain-of-trust rules below.

## Goal

Today each customer proves a vendor's phone number on its own, from 90 days of its own documents
(spec 0021), so a customer new to a vendor gets no call. When this spec is done:

- **One shared profile per real vendor** links that vendor's records at every customer, by a tax-ID
  fingerprint that Ramp computes (the mock plays Ramp).
- **The profile keeps a trusted set of phone numbers.** The first comes from outside anchors (spec
  0041), never from a customer or from the vendor's own entry. Every later one joins only when a call
  to an already-trusted number confirms it.
- **A vendor portal** lets a vendor ask to replace a trusted number. The request alone changes
  nothing. A **confirmation call** to the trusted number, where the callee types the new number's
  last four digits on the keypad, does.
- **A bank-change case for a linked vendor calls the profile's oldest trusted number** still in
  service, and goes to review if the profile changed in the 7 days before the change.

Rules served: R1 and R5 are unchanged. R2: a profile number is trusted before the change, so it
cannot come from the change request. R3: code decides every step. R4 and R8: the new call discloses
first and runs the speech guard. R6: every profile change is hash-chained. R7: anything unclear
leaves the profile unchanged. R9: no text from a call or document changes the trusted set.

## Decisions

- **D1. The linking key.**
  - The Ramp mock computes `tax_id_fingerprint = HMAC-SHA256(DIALBACK_NETWORK_KEY, normalized tax
    ID)` and returns it on the vendor representation. It never returns the tax ID itself.
  - The core stores the fingerprint in `app.vendors.tin_hash` on sync. Nothing in Dialback holds
    the key.
  - Vendors with the same fingerprint, in any dataset, are members of one profile. Nothing else
    links profiles: no names, domains or phone numbers.
  - With `DIALBACK_RAMP=sandbox` no fingerprint arrives, so nothing links (Q87).
- **D2. The first trusted number.** Spec 0041's `choose_anchor()` runs once per profile, on the
  registry and website sources, which neither the vendor's onboarding entry nor any customer
  supplies. Outcome `anchored` puts the anchor in the trusted set with `via = anchor`. Outcome
  `no_anchor` leaves the set empty, and the profile's cases use today's ledger selection. A number
  that only a customer entered, a sham customer included, is never trusted by the profile.
- **D3. The chain.** A number joins the trusted set only through a confirmed request (D5). No
  document can add or retire a trusted number: that would reopen FINDINGS A1.
- **D4. Combining evidence, never trust.**
  - Linking another customer's vendor adds a member and never adds to the trusted set.
  - If anchors computed for a profile's members disagree, the profile is marked `conflict`. It has
    no pick until a person resolves it.
- **D5. Requests and their outcomes.** A vendor user asks to replace trusted number T with new
  number N. The request records T, N and who asked. The core then asks the voice service for a
  confirmation call to T. Code decides the outcome:
  - **`confirmed`:** consent is yes, the company named matches the vendor, the answer to "did your
    company ask to change its phone number" is yes, and the keypad digits equal N's last four. N
    joins as `trusted` (`via = confirmed_by_call`, `vouched_by = T`). T becomes `retiring` with
    `retire_at = confirmed_at + waiting_days`.
  - **`denied`:** a clear "no" to the change question. Nothing changes. The profile gets a
    `change_denied` alert.
  - **`not_confirmed`:** anything else: no answer, no consent, the wrong company, wrong or missing
    digits, or a refused or failed call. Nothing changes.
  - Only the call can confirm a request: never the web, an email, a password reset or a person on
    the dashboard (Q90).
- **D6. The pick.** The profile's pick is its oldest trusted number still in service: `trusted`, or
  `retiring` before its `retire_at`. Its `trusted_since` must be before the case's cutoff. Ties
  break by `trusted_since`, then by E.164 order.
- **D7. Using the pick.** With `DIALBACK_PROFILE=on`, a case for a linked vendor whose profile has
  a pick (and is not in `conflict`) uses that pick.
  - It is written as a normal `ledger.contact_points` row and `ledger.number_selections` row for
    that dataset and vendor, with `rule_version = profile-select-v1`, source type `shared_profile`
    and a `profile` block. So the voice service's dial path and rule V01 work unchanged.
  - The ledger's own selection is still computed. Its outcome and number go in the `profile` block
    (`ledger_outcome`, `ledger_e164`) so the dashboard can show both.
  - In every other case, selection is exactly today's.
- **D8. The waiting period.** `waiting_days = 7`.
  - The `profile` block carries `changed_within_wait`: true if any trusted-set change (a number
    added, set to retiring or retired) happened within `waiting_days` before the cutoff.
  - A new tri-state fact, `profile_changed_recently`, is unknown when the pick is not from a
    profile. It feeds a new rule, `NR22_profile_changed_recently`.
  - The engine version becomes `decision-1.1.0`.
- **D9. Vendor users.**
  - A new table, `app.vendor_users (user_id, vendor_profile_id)`. Vendor users get no customer role.
  - Restrictive row-level security (the pattern in migration 0012) lets them read nothing in `app`,
    `ledger` or `voice`. Every customer endpoint answers them 403.
  - Vendor accounts are created by the seed, not by self sign-up. The demo sign-in page gets a "Sign
    in as Acme (vendor)" button (`DIALBACK_DEMO_VENDOR_EMAIL`, `DIALBACK_DEMO_VENDOR_PASSWORD`).
- **D10. What each side sees.**
  - A customer sees only derived facts on its own case, for example "From Acme's shared profile:
    anchored on Oct 1, 2026 (registry and website)" or "confirmed by a call on Nov 9, 2026". It
    never sees other customers, their numbers or their documents.
  - A vendor sees its own profile: trusted numbers as last four digits only, and its requests with
    their outcomes. It never sees which customers pay it.
- **D11. The confirmation call.** It follows 0042's pattern: its own script, table, trigger and
  guard. The case call is untouched.
  - **Script** (`packages/policy`, its own state machine):
    1. the fixed disclosure, naming `DIALBACK_PROFILE_CALLER_NAME` (Q88);
    2. consent;
    3. "Could you tell me the name of the company I've reached?";
    4. "Did your company recently ask to change the phone number on its vendor profile?" (yes or
       no, fixed wording);
    5. "Please type the last four digits of the new number on your keypad, then press pound.";
    6. a close that reveals nothing.

    It never says N, T, an outcome or what is checked (R4). Spoken digits are not accepted for the
    new number (FINDINGS A14). The relay already emits keypad digits on pound or after four keys.
  - **Storage:** `voice.profile_calls`: the request, the number, times, status, result, and the
    answers without digits. `new_last4_result` is one of `match`, `mismatch`, `not_provided`. Typed
    digits stay in memory, as they do today.
  - **Trigger:** `POST /profile-calls {request_id}` on the voice service, with the internal token.
  - **Guard,** separate from the case guard. It refuses the call if any of these fails or can't be
    read:
    - the request is `pending`;
    - the dialed number is the request's T, and it is trusted and in service;
    - the number has not opted out;
    - the call is inside the calling window;
    - the number has had fewer than 3 profile calls in 30 days;
    - the number is on `DIALBACK_DIAL_ALLOWLIST`.
  - **Twilio routes:** `/twilio/profile/twiml/{id}`, `/twilio/profile/status/{id}` and
    `/twilio/profile/relay/{id}`, signed and token-checked like the case routes.
  - **Report:** `POST /internal/profile-calls/{id}/ended` to the core, which compares the digits
    with N's last four and applies D5.
- **D12. Evidence.**
  - Each profile has its own chain, through `audit.append_evidence`.
  - Events: `profile_created`, `member_linked`, `anchored`, `conflict`, `request_created`,
    `confirmation_call_ended`, `number_trusted`, `number_retiring`, `number_retired`,
    `change_denied`.
  - Envelopes hold integers and strings only, and never digits typed on a call.
- **D13. The flag.** `DIALBACK_PROFILE` is `off` or `on`, safe default `off`, read in
  `apps/core/src/dialback_core/flags.py`. With `off`, no profile is read or written and selection
  is exactly today's.

## Contract touched

One contracts PR, approved by every owner. Every change only adds:

- **`schemas/selection.schema.json`:**
  - `shared_profile` in `SourceType`;
  - `profile_unavailable` in `Reason`;
  - an optional `profile` object: `vendor_profile_id`, `trusted_since`, `trusted_via`,
    `changed_within_wait`, `waiting_days`, `ledger_outcome`, `ledger_e164`.
- **`openapi/dialback.yaml`:**
  - the new reason in `SelectionView.reason_text`;
  - vendor endpoints `GET /vendor/me`, `GET /vendor/profile`, `POST /vendor/requests` and `GET
    /vendor/requests`;
  - regenerated types.
- **`openapi/internal.yaml`:** `POST /profile-calls` with its refusal codes, and `POST
  /internal/profile-calls/{id}/ended`.
- **`schemas/evidence-event.schema.json`:** the profile event bodies in D12.

Not a contract: the mock's `tax_id_fingerprint` lives in the mock's own model. Real Ramp is Q87.

## Acceptance

Linking and the first trusted number

- [ ] **P1.** The mock returns `tax_id_fingerprint` on every vendor that has a tax ID. Equal
  normalized tax IDs give equal fingerprints across businesses. The tax ID itself is never returned.
- [ ] **P2.** The core stores the fingerprint in `app.vendors.tin_hash`. Vendors with equal
  fingerprints in different datasets are members of one profile; a vendor without one is in no
  profile.
- [ ] **P3.** A profile's first trusted number is the 0041 anchor when the outcome is `anchored`;
  with `no_anchor` the set is empty. Linking never adds a number from a member's Ramp contacts or
  ledger documents, a sham customer's included.
- [ ] **P4.** Anchors that disagree mark the profile `conflict`: no pick, and an alert.

The portal and requests

- [ ] **R1.** A vendor user signs in and sees its profile: company name, trusted numbers as last
  four with how and when each was trusted, and its requests with their outcomes. Nothing about
  customers.
- [ ] **R2.** A vendor user can ask to replace T with N, where T is in its trusted set, N is valid
  E.164 and N is not already trusted. The request is `pending`, changes nothing, and triggers one
  confirmation call to T.
- [ ] **R3.** A vendor user gets 403 from every customer endpoint and reads no `app`, `ledger` or
  `voice` row through row-level security. A customer user gets 403 from every vendor endpoint.
- [ ] **R4.** A second request while one is pending is refused.
- [ ] **R5.** If the voice service refuses the confirmation call, the request ends
  `not_confirmed`, the vendor sees why, and nothing changes.

The confirmation call

- [ ] **C1.** The call discloses AI and recording first (R8), then asks consent, company, the
  change question and the keypad digits, in that order. It never says N, T, an outcome or what is
  checked (R4).
- [ ] **C2.** The outcome is `confirmed` only when consent is yes, the company matches, the change
  answer is yes and the keypad digits equal N's last four. Then N is `trusted` with `vouched_by =
  T`, T is `retiring` with `retire_at` 7 days later, and the chain has `number_trusted` and
  `number_retiring`.
- [ ] **C3.** A clear "no" to the change question gives `denied`, and a `change_denied` alert shows
  on the profile and in the vendor's portal. Nothing changes.
- [ ] **C4.** Every other path gives `not_confirmed` and changes nothing: no answer, voicemail,
  consent no, the wrong company, wrong or missing digits, spoken digits, a hang-up, a model error.
- [ ] **C5.** The guard refuses a call when the number is not the request's T, is not trusted and
  in service, has opted out, is outside the window, has had 3 profile calls in 30 days, or is off
  the allowlist. It also refuses when any of these can't be read.
- [ ] **C6.** Typed digits are never stored or logged. `voice.profile_calls` holds only
  `new_last4_result`.

Bank-change cases

- [ ] **S1.** With `DIALBACK_PROFILE=off`, every existing core, ledger, voice and decision test
  passes unchanged, and no profile table is read.
- [ ] **S2.** With `on`, a case for a linked vendor whose profile has a pick calls that pick. The
  selection row has `rule_version = profile-select-v1`, source type `shared_profile` and the
  `profile` block. V01 holds when the pick is dialed.
- [ ] **S3.** A customer with no ledger history for the vendor (cold start) still gets a call when
  the profile has a pick, even with no change request recorded. This is safe under R2 because the
  pick was trusted before the cutoff, and so before any request.
- [ ] **S4.** These are never the pick: a number trusted after the cutoff, or a retiring number
  past its `retire_at`. A profile in `conflict`, or one with an empty trusted set, falls back to
  today's ledger selection.
- [ ] **S5.** When the profile changed within 7 days before the cutoff, the decision is Needs
  review with `NR22_profile_changed_recently`, even if every V rule holds.
- [ ] **S6.** If the profile can't be read, nothing is picked: the outcome is `no_provable_number`
  with reason `profile_unavailable`, and a person decides (R7).

The dashboard

- [ ] **W1.** For a profile pick, the case's lineage panel shows the derived fact (D10) with the
  ledger's own outcome beside it. No other customer appears anywhere.
- [ ] **W2.** The portal pages work at phone width and need no customer dataset.

The demo

- [ ] **M1.** On top of `demo reset`, `dialback-core demo network [--phone acme=+1...]` seeds four
  fictional customers that pay Acme:
  - Fictional Co (today's dataset);
  - Harbor Supply Co, with two years of Acme documents;
  - Pinecrest Clinic, created last week, with none;
  - a sham, Quickstart Holdings, created 10 days ago, whose "Acme Fasteners LLC" carries Acme's tax
    ID and the number +1 206 555 0199.

  It links the profiles, runs the anchor and creates the Acme vendor user. `--phone` swaps Acme's
  anchor number for a team phone at run time; the team phone is never committed.
- [ ] **M2.** After `demo network`, Acme's profile has 4 members and 1 trusted number (the anchor).
  The sham's number is not trusted.
- [ ] **M3.** A bank change at Pinecrest calls the anchor. Today it would get no call.
- [ ] **M4.** A portal request to replace the anchor with +1 206 555 0199, answered "no" on the team
  phone, ends `denied` and shows the alert.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| P1 | `apps/ramp-mock/tests/test_tax_fingerprint.py::test_same_tax_id_same_fingerprint_across_businesses`, `::test_tax_id_never_returned` |
| P2 to P4 | `packages/profile/tests/test_link.py::test_links_by_fingerprint_only`, `::test_linking_never_adds_trust`, `::test_sham_member_number_not_trusted`, `::test_anchor_conflict_has_no_pick` |
| R1 to R5 | `apps/core/tests/test_vendor_portal.py`, and the vendor row-level security cases in the SQL tests run by `scripts/db/test.sh` |
| C1, C6 | `packages/policy/tests/test_profile_script.py::test_order_and_disclosure_first`, `::test_never_speaks_numbers_or_outcome`, `::test_spoken_digits_rejected` |
| C2 to C4 | `packages/profile/tests/test_requests.py::test_confirmed_needs_all_four`, `::test_denied_changes_nothing`, `::test_every_other_path_not_confirmed` |
| C5 | `apps/voice/tests/test_profile_calls.py::test_guard_refusals`, `::test_guard_refuses_on_read_error` |
| S1 | the existing suites with the flag `off` |
| S2 to S4, S6 | `apps/core/tests/test_profile_selection.py`, `packages/profile/tests/test_pick.py` |
| S5 | `packages/decision/tests/test_rules.py::test_nr22_profile_changed_recently` |
| W1, W2 | `apps/web`: `pnpm typecheck`, `pnpm build`, screenshots in the PR |
| M1 to M4 | `apps/core/tests/test_demo_network.py`, and the network beats in `scripts/dev/demo_rehearsal.py` |

## Build plan

Pull requests in order. Each stays under about 400 lines; after PR 1, PRs 2, 3 and 4 can run in
parallel.

| PR | What | Owner | Needs | Items |
|---|---|---|---|---|
| 0 | Merge 0041's anchor code from `sami/onboarding-anchors` | Sami (partner Inesh) | | P3 |
| 1 | Contracts (above) | all owners | | |
| 2 | `supabase/migrations/profile/` (profiles, members, numbers, requests, alerts, row-level security) and `packages/profile`: link, anchor, apply a call result, pick, settle retirements | Sami | 0, 1 | P2 to P4, C2 to C4, S4 |
| 3 | Mock fingerprint, `tin_hash` on sync, tax IDs in the demo seed | Abhi | | P1, P2 |
| 4 | Confirmation call: policy script, voice routes, table and guard; core `ended` handler | Sami; core handler Abhi | 1, 2 | C1 to C6 |
| 5 | Profile resolver behind the flag in `apps/core` ports; NR22, the fact and the engine bump | Abhi; ledger store helper Inesh | 2, 3 | S1 to S6 |
| 6 | Vendor users, row-level security, core vendor endpoints, `/vendor` pages, demo vendor sign-in, the lineage fact | Abhi (core), web owners | 1, 2 | R1 to R5, W1, W2 |
| 7 | `demo network`, the multi-dataset seed, rehearsal beats | Sami | all | M1 to M4 |

## Out of scope

- Bank details in the portal. A vendor can change phone numbers only.
- A notice call to the old number. In this flow, T is the number that confirms.
- Account recovery. Vendor accounts are seeded.
- Mailbox intake.
- ADR 0015's warning signals.
- The slow path into the profile: ledger history that promotes a number into the trusted set.
- Retiring a number by asking "is the old number still yours?" on a call.
- A tenant model for customers. Vendor users are fenced off (D9), but any customer user can still
  read every dataset.
- Real Ramp's fingerprint (Q87).

## Open questions

- **Q87. Real Ramp.** Can Ramp return a network tax-ID fingerprint, or compute one for Dialback?
  Until it can, `sandbox` never links.
- **Q88. Who the confirmation call is for.** No single customer applies. Proposed:
  `DIALBACK_PROFILE_CALLER_NAME` set to "Dialback vendor verification, for your customers on Ramp".
  Check it against R8 and the no-marketing rule.
- **Q89. The author's calls, to confirm.**
  - The portal changes phone numbers only.
  - `waiting_days = 7`.
  - A profile pick takes precedence over a provable ledger pick.
  - The profile call cap is separate from the case cap.
- **Q90. A dead trusted number.** When T no longer answers, the vendor cannot confirm anything. Is
  the fix a person with a new role, a fresh anchor, or nothing?
- **Q91. Ownership.** Proposed: `packages/profile` and `supabase/migrations/profile/` owned by Sami,
  with Inesh as partner.
- **Q92. The separate profile-combining work** mentioned on Nov 9 is not in the repo. This spec
  links only by tax-ID fingerprint; reconcile before PR 2.
- **Q93. The network key in production:** a key-management service and rotation. In the demo it is
  an env var on the mock, and nothing in Dialback holds it.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
scripts/db/test.sh
cd packages/profile && uv run pytest
cd packages/policy && uv run pytest
cd packages/decision && uv run pytest
cd apps/voice && uv run pytest
cd apps/core && uv run pytest
cd apps/ramp-mock && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
uv run python scripts/dev/demo_rehearsal.py
```
