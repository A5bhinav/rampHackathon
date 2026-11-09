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
- **Related to A8.**

### A11. The scripted agent's yes/no reading disagrees with the policy's

- **Status:** reported (voice re-review), low severity: the scripted agent is the test and dev-stack
  double, not the live agent.
- **What happens:** "We did." to the change question is recorded "no" (ungrounded), so NV01 decides
  not_verified (the not-verified rules run before NR14), and as an earlier call it would block every
  redial; "Yes, I think so." is recorded "yes" but the policy treats it as ungrounded.
- **Live calls:** `ClaudeAgent` asks the model for the yes/no value and the policy's regex then
  grounds it, so what live calls record for "We did." depends on the model and is untested by the
  lab (see C10). The double still runs live when `ANTHROPIC_API_KEY` is unset (`wire.py` logs a
  warning).

### A8. The company-name check accepts names that contain the vendor's

- **Status:** reported (voice-lab review); not yet evaluated.
- **What happens:** production's `every_name_matches` (`apps/core/src/dialback_core/company.py`)
  accepts "Acme Fasteners Holdings", "Acme Fasteners LLC" and "This is Acme Fasteners" for Acme
  Fasteners, by design (spec 0005, D7).
- **Open question:** whether a caller naming a related-but-different company should pass.

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

## D. Red teaming (not started)

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
- The voice branch commits carry "Claude Opus 4.8" in their trailers.
- Scratch worktrees exist under `/private/tmp/claude-501/...` (`fixtrial`, `voicereview`,
  `mainrun`). They are temporary, and the reproduction scripts in them may disappear.
- The 44 Postgres-backed ledger tests can't run on this machine; CI's `py` job runs them.
