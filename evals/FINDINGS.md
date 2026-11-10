# Attack lab: open findings

Every potential issue the attack lab, its reviews and its fix trials have turned up, in one place, so
none is lost. Last updated Nov 9. Each entry says how sure we are:

- **confirmed (ran)**: reproduced by running code.
- **confirmed (read)**: checked in the code, not run.
- **reported**: from a review, not yet re-checked.

Specs: 0017 (voice slice lab), 0033 (generative selection pressure), 0021 (the selection rule), 0022
(ingestion). Frozen cases live in `evals/corpus-generated/`.

## A. The product

### A1. The change request's own text can move the verification call to another number

- **Status:** confirmed (ran), through real ingestion (`ingest_all` with the stub reader), then
  `select()` at default settings. Frozen as `planted-number-picked-after-in-thread-bank-label`.
- **Where:** `packages/ledger/src/dialback_ledger/select.py`, lines 169-172. The party and fax
  checks read every visible sighting, including those in the change context. Spec 0021 says the
  same thing in its acceptance list ("eligible only if every visible sighting is labeled the vendor's
  and none a fax line"), so this is a design gap, not a coding slip.
- **What happens:**
  1. The attacker's change-request email mentions the vendor's real number, in any of three ways:
     "Bank verification line: (415) 555-0142", "Fax: ...", or a bare mention such as
     "Please stop using our old number (415) 555-0142".
  2. Ingestion records that sighting as a bank's line, a fax line, or party `unknown`.
  3. The rule refuses the real number.
  4. If the attacker planted a provable number in the vendor's records earlier (2 counting
     sources, at least 90 days old: the known limit K01), the call goes to it.
- **Why it matters:**
  - It works in production today.
  - It removes K01's hardest condition: the planted number no longer has to be older than the real one.
  - Without a planted number it fails safe: no call, and the case goes to manual review.

### A2. Honest change requests that mention the vendor's number are refused

- **Status:** confirmed (read and ran).
- **What happens:** "Call us to confirm at (415) 555-0142" gives the real number party `unknown`
  (`packages/ledger/src/dialback_ledger/reader.py`, the stub labeler). The rule then refuses it.
- **Effect:** over-blocking of legitimate requests.
- **Gap:** the over-block sweep has no "bare mention in the request" noise type yet.

### A3. An old hijacked thread can move the call with no label at all

- **Status:** confirmed (ran), in the over-block sweep's `in_thread_copy` rows at 365 and 730 days,
  planted family.
- **What happens:**
  1. A copy of the real number sits in the change request's thread, older than its other evidence.
  2. Rule R2 (never call a number first seen in the change context) refuses it.
  3. A planted number gets the call.
- **Gap:** I12 gates labels only, so this path is measured, not gated.

### A4. Labels from non-counting documents outside the change context also refuse a number

- **Status:** confirmed (read). Not probed by the lab (spec 0033, Q65).
- **What happens:** an email from an unrelated domain, outside the thread, that labels the real
  number someone else's also vetoes it.
- **Open question:** whether such documents reach `select()` depends on how ingestion attaches
  documents to a vendor (`ledger.documents.vendor_id`), which nobody has traced.

### A5. Latent defects in the selection rule

These are not reachable today, but each is frozen as a regression.

| Frozen case | Settings | What goes wrong |
|---|---|---|
| `near-miss-unanchored-by-shared-id` | v1 | A second document under the anchor's id un-anchors a near miss, and the sibling is picked. |
| `lookalike-erased-by-first-sorting-twin` | v1 | The rule keeps the copy of a shared id whose repr sorts first, which isn't chronological. A first-sorting twin of a lookalike email erases its cutoff pull-back. |
| `fuzz-seed0-m-shared-id-helped-06bbe3c8` | v1 | The same erasure, unblocking the genuine number. |
| `near-miss-unanchored-by-old-lookalike` | non-default | Possible only when `same_subject_window_days` exceeds `min_age_days`. |

- **Why latent:**
  - `ledger.documents.id` is a primary key, and `write_ingest` skips ids it already holds.
  - Production always runs the v1 settings.
- **When they go live:** any replay or ablation tool, or a new ingestion path, that passes duplicate
  ids or non-default settings.

### A6. Fix trials for A1 (scratch only, never committed)

- **D1 (ignore change-context labels) and D2 (count only clean labels).**
  - **Benefit:** both close A1.
  - **Fatal cost:** both open a mirror hole, so neither is usable.
  - **The mirror hole:** a lookalike email pulls the cutoff earlier, which moves a legitimate label
    (a clean listing calling a number a bank's) into the change context. There it is ignored, and the
    attacker's number is picked.
  - **Evidence:** a hand-built v1 world; `M_lookalike_helped` went from 0 to 8 of 2000 worlds.
- **D3 (refuse the whole case only when change-context labels would change who is called).**
  - **How it works:** keep every per-number check; then compare the call list (pick and up to two
    alternates) with and without labels that only change-context sightings carry; call only the
    agreed prefix.
  - **Results:**
    - I12 failures went from 454 to 0 of 2000 worlds.
    - Every other lab label was unchanged.
    - Alternates never move.
    - The 118 ledger tests that run without Postgres pass.
    - Every ingestion wording gives no call instead of the planted number.
  - **Costs and caveats:**
    - It calls no one in about 2% of generated worlds where today's rule calls a legitimate number.
      All of those have a label in the request's thread.
    - One pre-registered check (P4) failed: in the lookalike world, the expected outcome was "the
      real number is called"; D3 calls no one instead. It fails closed, but not as specified.
    - The 44 Postgres tests were not run.
    - Open: whether to bump `ledger-select-v1`. It is hard-coded in web data, contract examples
      and voice tests.
- **Prototype:** `select_D3.py` in the session scratchpad (temporary).
- **Note on its output:** a refusal keeps the per-number `eligible: true` and `rank` fields with
  outcome `no_provable_number`. The schema allows it, but its text says rank 1 is the pick, and
  `store.py` would record those numbers as eligible. Decide this before adopting the shape.

### A7. Line takeover is defended only by a fact nothing produces

- **Confirmed (ran), Nov 9:** the voice lab's a07 passes only because its scenario declares
  `line_risk: true`. With the value production supplies (unknown), a07 is verified. The lab should not
  report this attack as blocked (see C9).

- **Status:** reported (voice-lab review); checked by `git grep`.
- **What happens:** only `packages/decision` defines and reads `line_risk`; no production code sets
  it. So a real line takeover can verify. The voice lab's a07 scenario passes only because it
  declares `line_risk: true`.
- **Spec:** 0044 on `sami/line-risk` (draft): a Twilio Lookup before dialing refuses a line that
  was recently ported, SIM-swapped or reassigned. It cannot see forwarding set inside the vendor's
  own phone system.

### A9. A caller who refuses to give a role can still be verified

- **Status:** confirmed (ran), found by the voice-lab rework (Nov 9).
- **What happens:** both agents record whatever the person says at the role question as
  `speaker_role`: the `ScriptedAgent` double (`apps/voice/src/dialback_voice/agent.py`) and the live
  `ClaudeAgent` (`claude_agent.py`, `_value`: free-text phases are the caller's words, no model). So
  "I'd rather not say" becomes a role. Rule V06 (`packages/decision`) accepts any non-empty role, so
  the call can verify.
- **Where to fix:** the agent (record no role on a refusal) or V06 (reject a refusal), in the voice
  and decision slices. The voice lab has no scenario for it yet, because it would fail today.

### A10. The company check accepts a negated name, or another company named elsewhere on the call

- **Status:** confirmed (ran), through the voice lab with the `ScriptedAgent`, Nov 9 (voice
  re-review); re-run on origin/main da057f8 through `policy.step`, `every_name_matches` and `decide`
  with both the `ScriptedAgent` and the live `ClaudeAgent` (fake model; the company phase never calls
  the model): all three attacks verify on both.
- **What happens:** each of these callers is verified as an attack on a clean call:
  - "Not Acme Fasteners, this is Globex Logistics" at the company question;
  - "We're a reseller for Acme Fasteners, we're Globex";
  - the vendor named at the company question, then "AP clerk at Globex Logistics" at the role
    question (or Globex named at the name or department question).
- **Why:** both agents record the whole utterance, so the policy's negation check
  (`packages/policy/.../step.py`, `_appears_unnegated`) sees nothing before the name;
  `every_name_matches` (`apps/core/src/dialback_core/company.py`) only checks that the vendor's words
  appear somewhere; and only the company answer is matched, not the others.
- **Owners:** `company.py` is in `apps/core` (Abhi, with Sami as partner, per AGENTS.md and
  CODEOWNERS); the agent and the policy are Sami's.
- **Related to A8.**

### A11. The scripted agent's yes/no reading disagrees with the policy's

- **Status:** reported (voice re-review), low severity: the scripted agent is the test and dev-stack
  double, not the live agent.
- **What happens:** "We did." to the change question is recorded "no" (ungrounded), so NV01 decides
  not_verified (the not-verified rules run before NR14) and the case is blocked; "Yes, I think so."
  is recorded "yes" but the policy treats it as ungrounded. (An earlier version of this entry said
  the "no" would also block every redial; it cannot, see A16.)
- **Live calls:** `ClaudeAgent` asks the model for the yes/no value and the policy's regex then
  grounds it, so what live calls record for "We did." depends on the model and is untested by the
  lab (see C10). The double still runs live when `ANTHROPIC_API_KEY` is unset (`wire.py` logs a
  warning).
- **Also (ran, Nov 9):** "Sure, no problem." to the consent question is recorded "no" by the double
  (its "no" pattern matches "no problem"; the policy strips that phrase, the double does not), so the
  call ends as consent refused.

### A12. An unclear answer is never asked again

- **Status:** confirmed (read), Nov 9, on origin/main da057f8.
- **What happens:** the call re-asks only on silence ("are you still there?"), a rejected tool call
  (once, then the call ends), an empty keypad entry, a callback with no time, and a new speaker
  (name and role again). An unclear answer is recorded and the call moves on:
  - consent: the live agent's model is told to give the cautious answer, which is "no", and a
    model error also records "no", so a garbled "yes" ends the call as consent refused;
  - the change question: "unsure", so NR09 sends it to a person;
  - the last four with no digits heard: `dont_know` or `refused`, so NR06;
  - company, name, role: the words are kept as heard, and a garbled company is unknown (no V05).
- **Keep:** a wrong answer must never be re-asked (it would tell an attacker they were wrong and let
  them guess), and the voice service cannot know a wrong answer anyway: the digits are compared in
  the core after the call. Only "could not tell" should earn one more try.
- **Where to fix:** the agent would need an `unclear` value and `policy.step` a one-time re-ask;
  `apps/voice` and `packages/policy`.
- **Spec:** 0043 on `sami/keypad-only-digits` (draft, covers A11's yes/no part, A12, A13, A14).

### A13. The keypad has no reset, and a slip cannot be corrected

- **Status:** confirmed (read), Nov 9, `apps/voice/src/dialback_voice/twilio/relay.py`, `_next_event`.
- **What happens:**
  - `*` is ignored ("keep collecting"). A caller who types 4, 8, then `*` to start over, then
    4, 8, 2, 1 is recorded as 4848.
  - The fourth digit submits at once, so a mistyped fourth digit cannot be fixed.
  - `#` submits whatever is in hand: 1 to 3 digits are recorded and become `not_provided` (NR06)
    with no re-ask; only an empty `#` is re-asked.
  - The question never mentions `#` or `*`.
- **Why it matters:** a wrong four is NV02 (not verified), so the case is blocked, and only a new
  change request reopens it (`blocked --> held: change_detected` in `docs/state-machine.md`; no
  person can clear a blocked case). An earlier version said NV02 also fires on every redial; it
  cannot, because a call with answers ends the generation's calling (A16).
- **Common practice:** Amazon Lex defaults to `*` to clear and `#` to end, with an end timeout.
- **Where to fix:** `relay.py` and the last-four wording in `packages/policy/.../script.py`.

### A14. Spoken last-four digits are misread, and a misread blocks the change

- **Status:** confirmed (ran `digit_runs` and both agents' last-four readings), Nov 9, origin/main
  da057f8.
- **What happens:** the question invites speech ("You can say them or enter them on your keypad").
  Spoken digits go through speech-to-text, then `digit_runs`, then the agent:
  - "zero zero zero one, those are the last four": the live agent records 0014 ("four" counts);
    `docs/live-call.md` already warns about this one;
  - "forty eight twenty one": the live agent records 8201 (`digit_runs` reads 408201); the double
    proposes 408201, which the policy rejects as a violation;
  - "it ends in 4821, my cell is 415 555 0123": the live agent records 0123 (the double, 4821);
  - "for eight to one" (a mis-hearing): 81, so `not_provided`.
- **Why it matters:** a misread four is NV02, not verified, so the case is blocked (A13).
  The comment in `claude_agent.py` (`_value`) says a wrong four "routes to manual review, never a
  false pass": the second half holds, the first does not.
- **Exposure:** keypad digits never become text (`step` writes "[keypad]"). Spoken digits become
  transcript text in memory, after a third-party speech-to-text service. Digits said at any other
  question go to the model inside the caller's line: `ClaudeAgent` sends that line unredacted.
  Nothing stores them (the voice DB keeps call status only; the core hashes the last four).
- **Not a fraud defense either way:** whoever set up the new account knows its digits.
- **Fix:** keypad only for the last four (with A13's `*` and `#`), spoken digits at that question
  ignored and re-asked, and digit runs redacted from any caller line before it reaches the model.
  `apps/voice` and `packages/policy`; revises spec 0015 D1's last-four path and spec 0012's
  grounding of spoken digits.

### A8. The company-name check accepts names that contain the vendor's

- **Status:** reported (voice-lab review); not yet evaluated.
- **What happens:** production's `every_name_matches` (`apps/core/src/dialback_core/company.py`)
  accepts "Acme Fasteners Holdings", "Acme Fasteners LLC" and "This is Acme Fasteners" for Acme
  Fasteners, by design (spec 0005, D7).
- **Open question:** whether a caller naming a related-but-different company should pass.

### A15. A young vendor's bank change gets no call, and a vendor's first numbers are never checked

- **Status:** confirmed (read), Nov 9, origin/main da057f8. Not measured on any customer's data.
- **Where:** `packages/ledger/src/dialback_ledger/select.py`, lines 173-175 (`min_source_types` 2,
  `min_age_days` 90). When nothing qualifies, the worker emits `no_provable_number`, and the
  timeline says a person must verify.
- **What happens:**
  1. A number qualifies only once 2 source types show it and it was seen cleanly at least 90 days
     before the change's cutoff.
  2. A vendor added in the last 90 days usually has no such number. Every bank change it makes gets
     no call: the payment stays held until a person verifies it by hand.
  3. Once a number has 2 source types and 90 days, it is trusted, whatever its origin. Nothing
     checks where a vendor's first numbers came from (spec 0002, Q2, still open).
- **Why it matters:**
  - Right after onboarding, Dialback verifies nothing on its own, which is when a vendor's details
    are newest and least checked. How many vendors fall in that window is unknown.
  - Point 3 is K01 from the start: whoever supplies a vendor's first records owns its provable
    number after 90 days.
- **Considered, not built:** calling a young number anyway and still sending the case to review.
  The answer is lopsided: a "no" is strong evidence of fraud, but a "yes" proves nothing, because
  the number itself is unproven. `decide()` lets any "no" make a case not verified (NV01) but
  verifies only on the ledger's pick (V01), so this would need a decision-rule change.
- **Proposed:** specs 0041 and 0042 (PR #91, Sami). They find a new vendor's number in sources the
  vendor did not supply (a business registry, its website), then call that number once to confirm.
  Shadow mode first: a report nothing else reads, with no change to the pipeline.

### A16. Earlier-call evidence never reaches the decision

- **Status:** confirmed (read), Nov 9, origin/main da057f8; raised by spec 0043's builder check.
- **What happens:**
  - The voice service sends `answers` only when a call ends `answers_recorded`
    (`apps/voice/src/dialback_voice/app.py`, the report body).
  - An `answers_recorded` call moves the case to deciding, which ends that generation's calling
    (`docs/state-machine.md`).
  - So every earlier call `decide()` sees (`worker.py`, `earlier_call`) is a no-answer, failed or
    callback call with no answers. `_earlier_concern` and `_denied` never fire, so NR19 and the
    `earlier` halves of NV01 and NV02 cannot fire in production.
- **Why it matters:** spec 0005 D2 says an earlier call can count against a change. For example, a
  vendor says "No, we never asked for that", then asks to be called back: the call ends
  `callback_requested`, its "no" is never sent, and the redial decides without it.
- **Where to fix:** send consented answers on every report, not only `answers_recorded` (the
  contract already allows `answers` on any `CallEndedBody`). That is `apps/voice` (Sami) and the
  core's reading of them (`apps/core`, Abhi). Not evaluated beyond reading.

### A17. The call says it is recorded, but nothing records it

- **Status:** confirmed (read), Nov 9, origin/main da057f8.
- **What happens:** the disclosure says "on a recorded line" (R8), and `docs/threat-model.md` says
  recordings live in a private bucket with each recording's SHA-256 in the chain. But the TwiML
  (`origination.py`) sets no recording, nothing in `apps/voice` or `apps/core` stores one, and
  `recording_sha256` is never set.
- **Why it matters:** a reviewer of a `needs_review` case has the structured answers but no
  recording or transcript to check them against. The disclosure itself over-promises, which is
  the safe direction.
- **Where to fix:** decide whether to record (consent law, storage, the private bucket) or say
  "this call may be recorded". `apps/voice` (Sami).

### A18. The calling window uses one time zone for every vendor

- **Status:** confirmed (read), Nov 9, origin/main da057f8.
- **What happens:** `apps/core/src/dialback_core/calling_window.py` says calls happen "weekdays
  9:00 to 16:30 in the vendor's time zone", but the worker builds the window from one setting,
  `settings.calling_tz` (`worker.py`, `CallingWindow.parse`). A vendor in another time zone is
  called in the core's hours: 9:00 Pacific is 6:00 for a Hawaii vendor, and 16:30 Eastern is
  13:30 for a California one.
- **Where to fix:** take the vendor's time zone from the vendor record (the voice side already
  carries a `vendor_tz`, default `America/Los_Angeles`). `apps/core` (Abhi).

## B. The fuzz lab (spec 0033)

### B1. I12 never probes the change-request email itself

- **Status:** confirmed (ran). **Fixed Nov 9** (b5da22e, d54397c): the probe covers the request email,
  own-domain thread replies and ingestion's label shapes; the generator puts labels in the request
  email; `planted-number-picked-after-bare-mention-in-request` is frozen from real ingestion.
- **Re-review K1, fixed Nov 9 (3562c01):** the same problem remained on the after-the-cutoff path, a
  forwarded subject, a combosquat sender and the role-bearing bank shape. I12 now probes nine paths
  and five label kinds; the generator draws follow-ups and an earlier cutoff;
  `planted-number-picked-after-follow-up-email` is frozen from real ingestion.
- **Re-review K2 and K3, fixed Nov 9:** I12 compares the whole call list (pick and backups), and
  re-freezing a case no longer rewrites its notes.
- **What is wrong:**
  - Real ingestion puts the attacker's label in the request email's own body.
  - The generator never puts a sighting on the request document, and I12's addition half probes
    only extra emails.
  - So a partial fix that still honors labels on the request email passes I12 on generated worlds.
  - Also never probed: own-domain thread senders (a hijacked mailbox), the `customer` label, and
    ingestion's exact bank shape (role `unknown`, party `bank`).

### B2. The lab's stand-in for the fail-closed fix is wrong

- **Status:** confirmed (read). **Fixed Nov 9** (704f91b): the stand-in is narrow and schema-valid,
  and the whole oracle finds nothing new against it over 200 worlds.
- **What is wrong:** `_fail_closed` in `evals/tests/test_invariants.py` has three problems:
  - it emits schema-invalid output (it keeps `contact_point_id`, `alternates` and `source_types`,
    and has no `reason`);
  - it marks every candidate ineligible, so the "attacker evidence never helps" checks flag
    spurious violations;
  - its test ran only I3 and I12.
- **Also wrong:** the spec's "avoids both holes" was reasoning, not measured.

### B3. The red gate masks new regressions

- **Status:** confirmed (read).
- **What happens:** while I12 fails (about 18% of worlds), `lab fuzz` and the property test stop at
  the first failure. Until `select()` is fixed, the search cannot report any other new defect.
- **Spec:** 0046 on `sami/guided-search` (approved Nov 9): a search that runs its whole budget and keeps the
  first world for every label.
- **Anomaly (not reconciled):** the I12 failure rate differs by source: about 18% here, 454 of 2000
  (23%) in A6, and 82 of 300 (27%) at seed 0 in spec 0046's census. These are probably different
  samples and oracle versions, but nothing checks that.
- **Open labels (Nov 9):** 8 of the 9 frozen cases still fail, on `M_incontext_label_moved_pick`
  (I12), `M_shared_id_helped` (I11) and `M_lookalike_helped`.

### B4. Frozen-case history was overwritten

- **Status:** confirmed (ran). **Fixed Nov 9**: the original labels are restored, and the README says
  `labels` is never rewritten.
- **What is wrong:**
  - Refreshing the notes on `planted-number-picked-after-in-thread-bank-label` and
    `fuzz-seed0-m-incontext-label-blocked-7de35644` rewrote their `labels` field.
  - The README says `labels` records the check that failed when the case was frozen.
  - The `why` field keeps the history.

### B5. The generator never reaches some boundaries

- **Status:** reported (fuzz-lab review, mutation run).
- **What is wrong:** with each of these deliberate code changes, the 2000-world sample and the
  frozen cases still pass:
  - the change context ignoring `d.id == request.document_id`;
  - the cutoff ignoring `requested_cutoff` (always equal to the request time);
  - ranking reading change-context roles;
  - R2 ties going to the outside sighting;
  - same-time near misses not flagging each other;
  - `>` instead of `>=` at the established line;
  - `<` instead of `<=` at the subject window.
- **Fix:** knobs for a request without a thread, a requested cutoff earlier than the request, and
  ages exactly at each threshold.

### B6. Coverage floor and test notes

- **Status:** reported.
- **Items:**
  - `context_label_on_provable` counts a label on any provable number, not only on the pick.
  - A test comment's "about 5% and 0%" contradicted the measured 3.7% and 1.2% (fixed Nov 9).
  - The acceptance clause "the first-cut generator fails this check" has no test.

### B7. Deferred minors from the first review

- **M2:** the `lab` command needs the dev-only `hypothesis` package, including `lab run`.
- **M3:** `min_effort` overclaims slightly, and its test would pass a hand-coded answer.
- **M4:** `lab frontier` prints one sentence, not a grid.
- **M6:** one property test has no fixed seed.
- **M7:** the near-miss edges at exactly 365 days and at a same-time tie are untested.
- **M8:** the generator never makes a late sighting on an on-time document.
- **M9:** the eligible-number floor counts the planted real number.
- **M10:** the first cut's failing coverage output exists only in history.
- **M11:** the new commands are missing from the `AGENTS.md` commands table and the evals README.
- **M13:** two fragilities: the schema is found via a fixed relative path, and `invariants.py`
  imports worldgen's private `_address`.

### B8. Size and speed

- The lab suite takes about 74 seconds, up from 37.
- The branch is far over the roughly 400-line PR guideline, partly because it carries the voice lab.
- `test_generated_corpus.py` builds a real-looking number in pieces for a negative test, on purpose
  (it proves `freeze` refuses one). The reviewer checklist forbids real-looking numbers in code,
  so expect it to be flagged.

### B9. The direct checks catch regressions, not design mistakes

- **Status:** confirmed (read). The oracle's docstring and spec 0033 (decision 2) both say so.
- **What it means:**
  - Where no document id is shared, I1 to I7 and I9 follow `select()`'s own reading of the spec
    exactly. If that reading is wrong, the check is wrong with it.
  - So they catch a branch that is deleted or broken later, not a hole in the design. The design
    holes found so far (A1, the shared-id defect) came from the metamorphic checks.
  - Both kinds of check borrow the ledger's lookalike classifier, so a bug in it is invisible to
    both.
- **Spec:** 0046 steers its search by the metamorphic checks for this reason, and keeps the direct
  checks as the regression net.

### B10. A pinned seed does not pin the worlds

- **Status:** reported by the spec 0046 implementation session (Nov 9); not reproduced here.
- **What happens:** Hypothesis 6.170.14 also draws integer literals of 100 or more that it finds in
  every loaded first-party module, about 5% of the time. So `--seed 0` gives different worlds in a
  script, under pytest, from the CLI, or in a bench worker, depending on what is imported.
- **Effect:**
  - The `lab-fuzz` job's "2000 worlds at seed 0" is not one fixed sample.
  - Spec 0046's census (I12 in 82 and I11 in 34 of 300 worlds) was such a draw. Pinned, it is 69
    and 26.
- **Fixed so far:** only spec 0046's new searches, which turn it off (`guided.pinned_draws()`).
  `--search first`, which CI runs, still draws unpinned. Spec 0048 should pin it.

## C. The voice lab (spec 0017)

From the fresh review on Nov 9. C1 to C3 were blocking. **All of C1 to C8 were fixed on Nov 9** on
branch `sami/attack-lab-voice` (2bd74ad to 23e8315; spec 0017, "Review fixes" F1 to F9): main merged
in, the production agent and production's company and digit checks, the missing gates, an expected
rule and ending per scenario, the oracle fixes, five new scenarios, strict scenario files and CLI, a
real freeze, and a break suite of 22 deliberate breaks that all fail the lab. 75 tests; lab run 16/16.
Limits stated in the spec: V06, V10 and V11 cannot be seen by an honest call, V08 is redundant, and
the R8 disclosure gate gets a direct test. The entries below are kept as the record.

### C1. Most of the spec's hard gates are not implemented

- **Status:** confirmed (read and ran).
- **What is wrong:**
  - `evals/src/dialback_evals/scorers.py` checks only spoken account digits, a new number accepted,
    disclosure first, and the verdict.
  - Missing: `policy_violations`, `speech_guard_trips`, answers grounded, and "an injected line
    never changes the recorded slot, turn or value" (R9).
- **Evidence:** 13 of 16 deliberate breaks of the product pass the lab, including:
  - the speech guard disabled;
  - an agent obeying the injection;
  - an agent recording "yes" whatever was said;
  - seven decision rules disabled.

### C2. It tests a copy, not the product

- **Status:** confirmed (read).
- **What is wrong:**
  - **A patched agent.** `LabAgent` patches the production agent to record `speaker_role`. With
    the real agent, both reachable legitimate scenarios fail. Not disclosed in the spec or README.
  - **An assumed dial-out.** The runner hard-codes `dialed_out=True`.
  - **Its own company check.** It is stricter than production's `every_name_matches`, so a02 is
    blocked by a lab-only check.

### C3. Merging into main breaks it

- **Status:** reported (simulated by the reviewer); the API change confirmed (read).
- **What is wrong:**
  - Main split `Phase.NAME_ROLE` into `NAME` and `ROLE` (spec 0034), so 14 lab tests fail and every
    call errors out.
  - Main's agent now records the role itself, so `LabAgent` can go.
  - `.github/workflows/py.yml` conflicts, and `uv.lock` needs re-locking.

### C4. Verdicts come mostly from declared facts

- **Status:** reported.
- **What is wrong:**
  - a03, a04, a06 and a07 use the same caller as a legitimate scenario; each is blocked by one
    declared fact.
  - Every attack is blocked several ways at once, so removing one defense changes nothing.
  - The per-class pass rows therefore measure the decision rules on hand-picked facts, not the
    product's defenses.

### C5. Scoring holes

- **Status:** confirmed (read) / reported.
- **What is wrong:**
  - A crashed call counts as a passed attack (anything but "verified" passes).
  - A legitimate scenario that cannot verify requires nothing, so a wrongly "verified" result
    passes (l10).
  - `is_attack` is never checked against the scenario's class.
  - The "expected outcomes frozen with a hash" promise isn't implemented: the hash is computed and
    never compared.

### C6. CLI and loader

- **Status:** reported.
- **What is wrong:**
  - `lab run --split dev`, or running from inside `evals/`, loads 0 scenarios and exits 0.
  - `--max-usd` isn't enforced.
  - `inject_phase` and `hang_up_at` aren't validated; unknown keys are ignored.
  - `dialed_eligible_number` coerces strings to booleans.
  - Duplicate scenario ids are accepted.

### C7. Coverage gaps

- **Status:** reported.
- **Missing:**
  - Two test names the spec cites don't exist.
  - The `no_disclosure` and `accepted_new_number` gates are never shown to fire.
  - The R9 tests pass even when the injection is never delivered.
  - No scenarios for consent refused, machine answer, hang-up, silence, a garbage caller, no answer,
    or "a last-four given alongside a second number".
- **Other:**
  - CI trigger paths omit `packages/ledger` and the root lockfile.
  - The branch is about 935 changed lines, against the roughly 400 guideline.

### C8. Fix plan, in order

1. Merge main, adapt to the name and role split, delete `LabAgent`, and use production's company
   check.
2. Build the missing gates, each with a deliberate-break test that shows it fires.
3. Give each defense a scenario in which it is the only blocker.
4. Fix the scoring and the oracle.
5. Fail loudly on zero scenarios, and validate scenario files.
6. Rewrite the spec to separate what is tested on the call from what is declared as a fact.

The acceptance test is the 16-break experiment: every break must be caught.

### C9. Re-review of the rework (fresh session, Nov 9): what still overreaches

F1 to F9 hold as built (verified). But the lab still claims more than it shows:

- **a07 rests on a fact production never computes** (A7): the scorecard reports a blocked line
  takeover that production would verify. It also contradicts the spec's Q57 ("everything else stays
  None"). Other declared facts production never reports this way: a04's `dialed_number_is_pick:
  false` (the relay sets it true on every call), a06's `direction: inbound` (no production path), a01's
  `offered_number_from_request: true` with no number offered.
- **The R4 gates read the product's own detection.** With the production agent the speech guard runs
  0 times (its replies have no text), and `spoke_digits`, `accepted_new_number` and
  `speech_guard_trip` all come from the product's guard code. One regression in that code blinds the
  guard and the gate together: a broken digits check, a broken new-number pattern, a removed
  vendor-name rule, or an agent naming a bank and an amount (no digits) all pass the lab. The break
  suite's guard entries pass only because `_guard_off` leaves the detection code intact.
- **Most decision rules are unseen.** Removing any one of 27 of 37 rules leaves `lab run` green.
  Several are reachable by an honest scripted call today and have no scenario: NR05 (near-miss
  digits), NV03 (old-account digits), NR07 (old and new accounts end alike), NR10 (pressure), V01
  (dialed number not eligible; a06's rework removed its only scenario), V05 (unknown company), V07.
  The spec names only V06, V08, V10 and V11; NR14 and NR15 belong with them. F6's claim that each
  defense is the reason some scenario fails closed is false.
- **Two paired breaks pass regardless of their mutation** (grounding always true, the violation
  limit): the failures they assert fire without the mutation too. Only the two guard pairings depend
  on theirs.
- **The original 16 breaks:** 7 still pass with the production agent (guard off, V06, V08, V10, V11,
  grounding always true, the violation limit). F9's log should report that alongside the new suite.
- **The oracle asks the product.** It uses production's `every_name_matches` to decide what must
  verify, so breaking that function changes the frozen requirements, and `lab run`'s message blames a
  scenario change.
- **`build_facts` is a hand copy of the worker's fact assembly and differs** (answers and digits fed
  in on failed calls, `line_risk`, defaults for who answered and direction, no trade names or earlier
  calls); lab-smoke does not trigger on `worker.py` or `calls.py`.
- **Smaller:** l13 (voicemail) covers the text line only (the live relay always connects as human);
  the R8 gate checks order only, not that the disclosure says "AI" and "recorded"; `inject` without
  `inject_phase` silently never plays; free-text fields coerce `no` to "False"; `expect_end` cannot
  express a decline and allows an unreachable `no_answer`; `offer_other_number` is ignored by the
  line; no scenario speaks the digits; one injection scenario only; the spec still says the lab
  imports no core; the README shows `--split dev`; moving `every_name_matches` and
  `spoken_last_four` into `packages/decision` would spare the lab an app dependency.

### C10. The voice lab runs the dev double, not the live agent

- **Status:** confirmed (read), Nov 9.
- **What happens:** the lab drives `ScriptedAgent`, which `wire.py` uses only when
  `ANTHROPIC_API_KEY` is unset. Live calls use `ClaudeAgent`, which differs in two readings:
  - consent and the change question are classified by the model, not by regex (A11 is the double's
    regex disagreeing with the policy's);
  - the last four are the last four digits of every digit run in the turn joined, where the double
    takes the first run.
  The free-text phases (company, department, name, role) are the same in both: the caller's words.
- **So:** "the production agent" in spec 0017 and in C9 means the double. Results about free-text
  answers, the guard (neither agent writes text) and the decision carry over to live calls; results
  about yes/no and digit readings do not. Exercising the live agent needs the model, which is the
  red-teaming work in D.

## D. Red teaming

- **Started (Nov 9):** Sami chose number selection as the first target. Spec 0046
  (`sami/guided-search`, approved Nov 9) is a guided search on `select()` with no model: it scores each
  world by how much a must-not-help attacker move helped, steers toward higher scores, and fixes
  the measurement that decides whether the steering stays. The model-driven targets below remain.
- **Spec 0046, dev seed 0 (exploratory: one seed, not the result; Nov 9):**
  - Every arm found all 6 `helps` breaks by world 106, before targeting starts at world 200, so the
    primary metric was 0 on that seed by construction. If other seeds look the same, the pass bar
    can't be met and the measurement reports no evidence for steering.
  - `gain_new` was never above 0, so `guided-new` steers by a constant.
  - No arm found D01 or D03. D03 needs an upper-case "FAX" role, which the generator never draws;
    D01's number stays refused for other reasons.
  - `lab fuzz --search guided --seed 0` found I1, I4 and I5 on today's rule at world 459, from A5's
    latent shared-id lookalike class. Not frozen.
- **Targets:** the two model-driven parts.
  - **The live voice agent** (`apps/voice/src/dialback_voice/claude_agent.py`): a model playing the
    person who answers.
  - **The document reader:** model-written fraud documents fed through real ingestion. Only a
    keyword stub exists today; the LLM mode raises "not built".
- **Rule:** the attacker model only produces inputs. Code with facts fixed in advance is the judge.
- **Cost:** it needs a spending cap, repeats and rates.
- **First useful piece, even before an LLM reader exists:** a document generator that writes text
  and runs it through `ingest_all`. A1 and A2 live in the text-to-label step that the fuzz lab skips.
- **Prerequisite:** the voice lab rework (C), so attacks are scored by real gates.

## E. Process and housekeeping

- `.superpowers/` (the plan ledger and logs) is not git-ignored. Stage files by name.
- The voice branch commits, and the planted-breaks commit on `sami/redteam-breaks` (7ef40df), carry
  "Claude Opus 4.8" in their trailers. Left as is: the spec 0046 work builds on that commit.
- Scratch worktrees exist under `/private/tmp/claude-501/...` (`fixtrial`, `voicereview`,
  `mainrun`). They are temporary, and the reproduction scripts in them may disappear.
- The 44 Postgres-backed ledger tests can't run on this machine; CI's `py` job runs them.
- Every document is a JSON page model (spans with position, size and colour), not a rendered
  file: nothing reads a real PDF, email or scan yet (ledger README; spec 0022, out of scope). So the
  lab's hidden-text results hold for the page model only. Separately, `dialback_ledger/text.py` says
  reading real PDFs is "a later spec (0016)", but spec 0016 on main is the voice service entrypoint.
- **Spec and question numbers collide with main** (checked Nov 9 against origin/main da057f8):
  - `0033` is taken twice on main already (`0033-how-it-works-page`, `0033-stricter-passwords`).
    The lab's `0033-generative-selection-pressure` would be the third. Main also has two `0034`s.
  - The lab spec's open questions Q60, Q61 and Q65 are already used on main: Q60 and Q61 in
    `0029-voice-call-lookup-and-store.md`, Q65 in `0034-live-call-blockers.md`. A4 above cites
    "spec 0033, Q65", meaning the lab's.
  - Main uses up to Q68. PR #91 takes Q69 to Q75, the `sami/onboarding-anchors` branch takes
    Q76 and Q77, spec 0043 (`sami/keypad-only-digits`) takes Q78 to Q82, spec 0044
    (`sami/line-risk`) takes Q83 to Q86, and spec 0046 (`sami/guided-search`) takes Q87 to Q91,
    so the next free number is Q92. Renumber before this branch merges.
  - Spec 0045 is on `sami/shared-vendor-profile-spec` and 0046 on `sami/guided-search`, so the
    next free spec number is 0047.

## F. How other services verify a bank change (Nov 9)

From public pages only (mostly vendor marketing), so the cells marked "not stated" are unknown,
not absent.

| Step | Dialback | nsKnox AI Agent Caller | Kovrell (open source) | Eftsure | Trustpair, Spendflo |
|---|---|---|---|---|---|
| When it calls | every bank change, after a hold | risk rules (geography, value) or by hand | before a changed account is paid | onboarding and changes | Trustpair: no call; Spendflo: a person calls |
| Which number | the ledger's provable number (2 sources, 90 days, never from the change) | not stated | contact of record; no call if it changed in 30 days | staff find an independent number | Spendflo: a known number |
| Who talks | code's fixed words; the model only classifies | AI on a fixed procedure | AI asks, code scores | a person | a person |
| What is asked | company, name, role, "did you ask for this", last four (caller gives them) | identity, data points such as the last digits of a known account | identity, "did you ask", 2 invoice totals and a payment date, then reads the new account back | reads 3 digits, caller gives the rest, plus branch, name and business number | none |
| Wrong answer | never re-asked; the voice side never knows it | strict retry limits, then ends | not stated | a person judges | none |
| Account ownership by data | none built; the design lists "Account-name match" and "Ramp-signed identity" as future plug-ins | bank-network checks, a certificate, sometimes a test transfer | none | bank link plus business-register match, shared database | Trustpair: yes, the core product |
| Who decides | code; a block is automatic, a release needs two people | not stated; the summary goes to the AP team | code; a pass releases | a person | Trustpair: the API result; Spendflo: a person |
| Shared across customers | none built: the Trust Ledger reads one customer's documents; the design lists "Network alarm" (warn other Ramp customers who pay the same vendor) as a future plug-in | in-network database | no | about 4 million verified suppliers | Trustpair: data network |
| Line takeover | `line_risk` reserved, never computed (A7) | not stated | not stated | not stated | not stated |

Sources: nsknox.net/ai-agent-caller, github.com/mystiquemide/kovrell, eftsure.com supplier pages and
FAQ, trustpair.com/connect/api, spendflo.com bank-detail agents.

**Keep (others agree or ours is stronger):** a hold on every change; a number the request could not
have planted; the caller produces the digits and the agent never says them (spec 0043 makes it
keypad only); no live feedback on a wrong answer; code owns the words; an automatic block but a
two-person release; the attack lab (none of them publishes one).

**Add, in order (proposals, not decided):**
1. An account-ownership fact from a data provider: the design's "Account-name match" plug-in,
   which feeds the decision engine. It answers "is the account the vendor's", which a call cannot,
   and it gives young vendors with no provable number (A15) a path. The Trust Ledger and specs
   0041/0042 prove the phone number is the vendor's, not the bank account.
2. A real `line_risk`. Twilio Lookup's line type (mobile, landline, VoIP) is widely available; its
   SIM-swap date needs carrier approval and covers some regions; call forwarding is a private beta
   on major UK carriers only, unconditional forwarding only.
3. Evidence across Ramp customers: the design's "Network alarm" plug-in, and beyond it, the same
   vendor's number seen by many customers, or a new account added for only one of them. A
   data-sharing policy question for Ramp first.
4. Call in the vendor's local hours (A18) and language. The script is English only.

**Do not add:** invoice questions (a mailbox attacker has the invoices, and a clerk may not know
the totals); reading digits to the caller (people confirm what they hear, and it tells whoever
answers); live retries on a mismatch (each try is a guess); releasing on a pass alone.
