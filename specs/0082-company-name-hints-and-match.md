# 0082: Hear the vendor's name, and accept it the way people say it

**Owner:** Sami (author; Part A is `apps/voice` and `packages/policy`, Sami's. Part B is
`apps/core/src/dialback_core/company.py`, Abhi's, and Abhi reviews it.)
**Slice:** call and adversary  **Target day:** Tue Nov 10

## Goal

The call asks "Could you tell me the name of the company I've reached?" and never says the vendor's
name first. Both agents record the caller's **whole turn**, as said, as the `company_named` answer
(`apps/voice/src/dialback_voice/agent.py`, `claude_agent.py`), for example "Hi, this is Acme".

After the call, `every_name_matches` (`apps/core/src/dialback_core/company.py`) checks that answer
against the vendor's legal name and trade names:
- It returns `True` only if the answer contains every word of one of those names (in any order,
  after dropping case, punctuation and legal suffixes).
- Otherwise it returns `False`, which fires `NR03_wrong_company` (needs review).
- It returns `None` when nothing in the answer can be read.

On the first real call, the person said "Acme", speech-to-text wrote "acne", and the case went to
review on NR03. A correctly heard "Acme" fails too:
- The demo vendor's legal name is "Acme Fasteners LLC".
- Nothing fills `app.vendors.trade_names` (spec 0005's open question).

**What the company answer is worth.**

- **An innocent party on a wrong or reassigned number** cannot type the new account's last four,
  so V08 already stops the case.
- **A fraudster who holds the line we dial** gets no help from it. This covers forwarding on a
  compromised account, a port-out, a reassigned number they now own, or a number planted in old
  documents.
  - In production nothing else stops this caller today. Strict mode (S01) is off
    (`worker.py` calls `decide(facts)` without `strict`), and nothing sets `line_risk`, so NR21
    never fires.
  - Example `packages/decision/tests/examples/21b-forwarded-line-without-signal.json` records this
    as the system's stated limit.
  - The company answer is then the only check on what the caller knows about the vendor. A
    fraudster running this scam knows the vendor's name, so it passes under any rule. The old,
    stricter rule only helped where Ramp's legal name differs from the name the fraudster uses.
  - This gap belongs to line-takeover detection (NR21) and known contacts (S01), not to this
    check.
- **A clearly different company named on a call that otherwise passes** (an outsourced AP firm, a
  parent company) is worth a person's look. This is the one signal worth keeping.

So this spec fixes mishearings at the source (Part A). It accepts the short names people actually
say (Part B), and keeps the clear-mismatch signal.

## Decisions

### Part A (`apps/voice`, `packages/policy`, Sami): speech hints

1. **The TwiML carries the vendor's names as `hints`.** ConversationRelay's `hints` attribute is "a
   comma-separated list of words or phrases that may appear in the speech" (Twilio's TwiML
   reference). It makes the recognizer prefer those words when the audio is close ("acne" or
   "acme"). It cannot turn clearly different audio ("Globex") into the vendor's name.
   - The names are the call's `vendor_names`, the vendor's legal name and trade names from
     `app.vendors`. The route already has them (`app.py`, `CallDial.vendor_names`), so no extra
     lookup is needed.
   - **Where the names come from.** It is Ramp's current vendor record. The sync overwrites
     `legal_name` from Ramp's vendor name on every sync, including the one that detects the bank
     change (`detection.py`). So a clerk who edits the vendor's name along with the bank details
     changes both the hints and Part B's target.
   - For the matcher that fails safe: the real vendor answers with its real name, which gives NR03
     and a review. For hints, it biases the recognizer toward the edited name (Q138).
2. **The hint list.** For each vendor name, in order:
   - **the name as written.** Control characters (Unicode category C) are dropped, each comma
     becomes a space, and spaces are trimmed and collapsed. A name that is empty after this is
     skipped.
   - **its first word as written,** with surrounding punctuation stripped. It is included only
     when the speech guard would accept that first word on its own (D6): a multi-word name whose
     first word, after the guard's cleaning and suffix list, has at least 4 characters and is not
     in the guard's common-first-word list.
     - Add a public helper for this test to `packages/policy/src/dialback_policy/guard.py`, and use
       it in `vendor_variants`, whose output must not change.

   Duplicates are dropped case-insensitively (the first spelling wins). A hint longer than 100
   characters is skipped. At most 10 hints are sent. With no hints, the attribute is left out.

   Examples:
   - "Acme Fasteners LLC" gives `hints="Acme Fasteners LLC,Acme"`.
   - "The Acme Group" gives `hints="The Acme Group,Acme"`.
   - "First Solar Inc" gives `hints="First Solar Inc"` ("first" is common).
   - "Yes Energy LLC" gives `hints="Yes Energy LLC"` ("yes" is under 4 characters).
3. **It is a bias, on purpose, and it applies to the whole call.** Twilio documents one `hints`
   attribute per session, not per question.
   - **The company check leans toward passing.** That is acceptable for the reasons in the Goal.
   - **Other free-text answers** (the person's name, their role) may come out spelled like the
     vendor's name when they sound close. That is accepted.
   - **Unaffected:** digits come only from the keypad. The yes/no answers are affected only through
     a full-name hint that contains "yes" or "no", and D2 never sends those words alone.
   - **Never spoken:** the callee learns nothing (R4), and the question still never says the name
     first.
   - **Escaped:** the value goes through `quoteattr` like every other attribute, and control
     characters are already gone (D2), so the TwiML stays well-formed. It is a hint to the
     recognizer, not an instruction to a model (R9).
4. **Nothing else in the TwiML changes.**

### Part B (`apps/core`, Abhi's code; Sami builds, Abhi reviews): accept the short name

5. **The input is a sentence.** The matcher reads the caller's whole turn, so any loosening applies
   to every word of it. That rules out fuzzy word matching (D8).
6. **One more way to match.** An answer is `True` if:
   - today's rule holds (every word of a name, any order); **or**
   - any of the vendor's name variants appears in the answer as a whole-word phrase.

   The variants are exactly `dialback_policy.guard.vendor_variants(names)`, the same list the
   speech guard uses to spot the vendor's name:
   - the name without legal suffixes ("acme fasteners");
   - the name run together ("acmefasteners");
   - the first two words of a name of 3 or more words;
   - the first word of a multi-word name, when it has 4 or more characters and is not in the
     guard's common-first-word list ("acme"; not "first", "general" or "main").

   The answer is tokenized the way the guard tokenizes text (NFKC, lowercase, runs of `[a-z0-9]`).
   A variant matches only as a contiguous run of whole tokens. The guard's run-together substring
   check (`_says_vendor`'s `compact`) is **not** used, since it is deliberately loose for catching
   the agent.
7. **`False` and `None` are as today.** Anything not `True` under D6 keeps today's result: `False`
   when the answer has readable words, and `None` when it has none. The several-answers rule is
   unchanged (any `False` gives `False`; otherwise any `None` gives `None`; otherwise `True`).

   **7a. Refusal words override a match** (amendment, Sami, Nov 10). An answer that says the person
   is not with the vendor, or only works with it, is `False` (NR03) even when the vendor's name is in
   it. It keeps the "clearly a different company" signal the Goal wants, at almost no cost to honest
   answers.
   - **The words,** read as whole words the way the guard reads text: `not`, `isn` (from "isn't"),
     `aren`, `wasn`, `weren`, `never`, `former`, `formerly`, `until`, `reseller`, `resellers`,
     `resell`, `resells`, `distribute`, `distributes`, `distributor`, `behalf`.
   - **The phrases,** as runs of whole words: "used to" and "I mean".
   - **Never these:** bare "no" and "sorry". Honest answers start with them ("No problem, this is
     Acme", "Sorry, Acme Fasteners").
   - **A word in the vendor's own name doesn't count** ("Never Summer Industries" answering "Never
     Summer").
   - **What it catches:** "Not Acme, this is Globex", "This isn't Acme", "We're a reseller for
     Acme", "We distribute for Acme", "I used to work at Acme", "I was at Acme until last year",
     "Acme, sorry, I mean Globex".
   - **What it doesn't:** a correction with no such word ("Acme? No, Globex"), and another company
     whose name contains the vendor's ("Acme Fasteners Supply Co"). Both stay accepted costs (D9).
   - **Its cost:** an honest answer that happens to use one of these words goes to review ("Not
     sure, I think this is Acme"). That is the safe direction (R7).
   - **The list is exploratory.** It was chosen after reading the lab's phrase bank (spec 0048), so
     its effect there proves nothing. D10's measurement is what confirms it.

   **7b. The decision (Sami, Nov 10): the company answer no longer has to match** (amendment). D7a
   stands as written. Where D7, D8, B2 to B5, "Contract touched", "Considered and rejected" or "Out
   of scope" say otherwise, this wins.
   1. **The company answer is no longer required to verify.** `V05_company_matches` is retired: it
      leaves `rules.VERIFIED`, and `V05_company_matches` and `V05_company_matches_unmet` keep their
      sentences in `engine.RETIRED`, so old decisions still render. A short ("Acme"), garbled
      ("acne") or missing answer does not block.
   2. **`NR03_wrong_company` keeps its id and its predicate** (`company_matches is False`), but now
      fires only for an answer with a refusal word or phrase (D7a). Its sentence becomes "The
      company answer says the person is not with this vendor, or only works with it."
      `SLOT_RULES["company_named"]` becomes `("NR03_wrong_company",)`.
   3. **The matcher** (`company.py`): an answer with readable words that neither matches nor holds a
      refusal word is `None`, not `False`. `every_name_matches` keeps its rule (any `False` gives
      `False`; otherwise any `None` gives `None`; otherwise `True`).
   4. **`ENGINE_VERSION` becomes `decision-1.1.0`.** A decision recorded by `decision-1.0.0` shows
      its checklist as "not checked", with a note. Known limit: an old decision that recorded NR03
      renders with the new sentence.

   | Answer | `company_matches` | Engine |
   |---|---|---|
   | names the vendor, with no refusal word | `True` | nothing fires |
   | holds a refusal word or phrase ("Not Acme, this is Globex", "We're a reseller for Acme") | `False` | NR03, needs review |
   | anything else: another company ("This is Globex"), a garbled name ("acne"), nonsense, no readable words, no answer | `None` | nothing fires |

   - **"This is Globex" passes.** Code can't tell another company's name from a garbled vendor
     name. The person who releases sees the answer on the call record.
   - **Why.** Requiring a match sent honest calls to review ("acne" on the first real call), and the
     Goal shows it gives no protection against a fraudster on the line, who knows the vendor's
     name. The signal kept, a person saying they are not with the vendor, stays as NR03.
   - **What it gives up.** A clearly different company named with no refusal word, on a call that
     otherwise passes, is no longer flagged; only the releaser sees it. Conflicting companies across
     answers are not flagged either (D9).
   - **The rule table changes, the contract doesn't.** `company_matches` stays `bool | None`, and
     `rules_fired` is still a list of ids.
   - **Q137 is answered:** decided: demote, with NR03 kept for refusal words; Sami, Nov 10.
8. **No fuzzy matching.** A one-letter slip rule was drafted and dropped after the builder check:
   - On sentences it accepts "help" for "Yelp", "this" for "Thin Air", and "Bell" for "Dell".
   - Phonetic codes disagree with each other: Soundex gives "acme" and "acne" the same code,
     Metaphone doesn't.
   - So "acne" stays `False` (NR03) unless Part A's hint fixes the transcript. That is a deliberate
     choice, pinned by a test.
9. **Accepted costs.**
   - A different company that shares a distinctive first word passes ("Acme Bolts" for "Acme
     Fasteners").
   - A correction with no refusal word passes ("Acme? No, Globex"), as it does today with the full
     name. Negations, resellers and past connections no longer pass (D7a). The lab has measured
     these classes of answer: `evals/FINDINGS.md` A8 and A10 on `sami/attack-lab-generative`.
   - Conflicting companies across answers are not flagged, because NR18 leaves companies to NR03
     and both answers match.
   - The digits must still match in every case.
10. **Part B is exploratory until measured.** These are the measurements, written before running
    anything. If the lab's company fuzzer (spec 0048) is run on the same seeds before and after
    Part B, report both numbers:
    - over-blocking, the share of answers that name the vendor but are not `True` (18.3%, 533 of
      2,920, in A10);
    - other-company acceptance, the answers naming someone else that come out `True`;
    - for D7a, the answers each refusal word turns to `False`, per phrase class.

    The expectation is that over-blocking falls and other-company acceptance rises. That is the
    accepted cost in D9. Neither number gates the merge; both go to Abhi with Q137.
    - **When D7a lands,** the lab's known-failures entries for negation, reseller and past
      connection should start passing and must come off (spec 0048, D3). Correction and "contains
      the vendor's name" stay listed.

### Considered and rejected

- **Demote the check entirely.** It loses the "clearly different company, right digits" signal,
  which costs nothing to keep.
  - D7b demotes it after all, keeping NR03 for refusal words; the plain "different company"
    signal is given up.
- **One-letter slips, and phonetic matching.** See D8.
- **The first word alone, whatever it is** (the first draft). On sentences it accepts "Hi, this is
  Globex" for "Thin Air" and "the main line at Globex" for "Main Street Supply". The guard's
  common-word and 4-character limits exist for exactly this.
- **Filling `trade_names`.** That is a sync change against Ramp's vendor fields, and its own spec.

## Contract touched

None. `company_matches` stays `bool | None`, and the decision rules don't change.

D7b changes the decision rules (V05 retired, NR03's sentence, `decision-1.1.0`), but no contract:
`company_matches` stays `bool | None`.

## Acceptance

Part A (`apps/voice`, `packages/policy`):

- [ ] A1. `build_connect_twiml` with vendor names adds `hints` per D2. "Acme Fasteners LLC" gives
  `hints="Acme Fasteners LLC,Acme"`, and the other three examples in D2 give what D2 says.
- [ ] A2. Commas become spaces, spaces are trimmed and collapsed, and a whitespace-only name gives
  no hint. Duplicates are dropped case-insensitively, a hint over 100 characters is skipped, and at
  most 10 hints are sent.
- [ ] A3. Quotes, ampersands and angle brackets are escaped, and a name containing a control
  character (for example `\x0b`) has it dropped. The TwiML parses as XML, and the attribute reads
  back exactly as the hint list.
- [ ] A4. With no hints, the TwiML equals `build_connect_twiml` called without names. This is
  compared against the function, not a frozen string, since open PR #102 edits the same line.
- [ ] A5. The TwiML route passes the looked-up call's `vendor_names`. The vendor's name appears in
  `hints` and nowhere in `welcomeGreeting` (R4).
- [ ] A6. `vendor_variants`'s output is unchanged after the helper is extracted. Existing policy
  and voice tests pass unedited.

Part B (`apps/core`), each with the caller's whole sentence as input, and "Acme Fasteners LLC" as
the vendor unless stated:

- [ ] B1. Short names match: "Acme", "This is Acme", "Hi, Acme accounts payable" and
  "AcmeFasteners" are `True`.
  - Today's matches still match: "Acme Fasteners" and "Fasteners, Acme" are `True`.
  - For "Brightline Office Supply Inc", "Brightline Office" is `True`.
- [ ] B2. Clearly different companies stay `False`, including near-sounding and common-word cases.
  These come from the builder check. (D7b: none of them is a match, and each is now `None`; see B9.)

  | Said | Vendor |
  |---|---|
  | "Globex Corporation" | Acme Fasteners LLC |
  | "Hi, this is Globex, how can I help you?" | Yelp Inc |
  | "This is Globex" | Thin Air LLC |
  | "You've reached the main line at Globex" | Main Street Supply LLC |
  | "General Dynamics" | General Electric Company |
  | "Bell Canada" | Dell Inc |
  | "We're a plumbing firm, Globex" | A & B Supply |

- [ ] B3. No fuzzy matching (D8): "acne" and "This is acne" are `False`. (D7b: not a match, and now
  `None`; see B9.)
- [ ] B4. A common first word alone doesn't match: "First" is `False` for "First Solar Inc", and
  "Main" is `False` for "Main Street Supply LLC". (D7b: not a match, and now `None`; see B9.)
- [ ] B5. Unchanged:
  - "LLC" alone and no answers give `None`.
  - Any `False` among several answers gives `False`.
  - A vendor with no usable name gives today's result.
  - The existing tests in `apps/core/tests/test_worker.py` pass unedited.
  - (D7b: a vendor with no usable name now gives `None` for an answer with readable words, and the
    company tests in `test_worker.py` change as B9 says.)
- [ ] B6. The matcher reads names as the guard does: for each B1 example, the variant that matches
  is in `vendor_variants`.
- [ ] B7. The matcher stays pure and total. Empty, suffix-only and non-ASCII names, and empty
  answers, never raise.
- [ ] B8. Refusal words override a match (D7a).
  - Each "what it catches" example in D7a is `False`.
  - Honest answers stay `True`: "No problem, this is Acme", "Sorry, Acme Fasteners", "Yes, Acme",
    and "Never Summer" for "Never Summer Industries".
  - "Acme? No, Globex" stays `True` (an accepted cost), and "Not sure, I think this is Acme" is
    `False` (the safe cost). Both are pinned.
- [ ] B9. A plain mismatch is unknown (D7b), and only a refusal is `False`.
  - `None`: every row of B2's table, B3's "acne" and "This is acne", B4's "First" and "Main", D6's
    whole-word cases, and B5's "no usable name" rows that have readable words.
  - "This is Globex" and nonsense are `None` for "Acme Fasteners LLC".
  - B8's refusals stay `False`, and `every_name_matches(["This is Globex", "Not Acme, this is
    Globex"])` is `False`.
  - End to end (Postgres): an extra answer "Globex Corporation" doesn't fire NR03, and the call
    decides exactly as it does without that answer. "Not Acme Fasteners, this is Globex" fires NR03.
- [ ] B10. The engine (D7b).
  - `V05_company_matches` is not in `rules.VERIFIED`, and neither it nor `V05_company_matches_unmet`
    is a current id. Both render from `engine.RETIRED` with their old sentences.
  - NR03's sentence is D7b's, its `FIRES` case (`company_matches=False`) still fires it alone, and
    `SLOT_RULES["company_named"]` is `("NR03_wrong_company",)`.
  - `ENGINE_VERSION` is `decision-1.1.0`.
  - The clean call (`GOOD`) verifies with `company_matches=None` and with `True`.
  - A worked example, another company named with no refusal word (`company_matches` null) and
    everything else clean, is verified.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| A1, A2 | `apps/voice/tests/test_twiml.py::test_hints_carry_the_vendor_names` (parametrized) |
| A3 | `apps/voice/tests/test_twiml.py::test_hints_are_escaped_and_read_back` |
| A4 | `apps/voice/tests/test_twiml.py::test_no_vendor_names_means_no_hints` |
| A5 | `apps/voice/tests/test_twiml.py::test_the_route_hints_the_calls_vendor_names_and_never_speaks_them` |
| A6 | the existing `packages/policy` and `apps/voice` suites, unedited |
| B1 to B7 | `apps/core/tests/test_company.py` (new, pure, no database) |
| B8 | `apps/core/tests/test_company.py::test_b8_refusal_words_override_a_match`, `::test_b8_honest_answers_and_pinned_costs` |
| B5 | the existing `apps/core/tests/test_worker.py` company tests, unedited (they need Postgres); D7b edits them (B9) |
| B9 | `apps/core/tests/test_company.py::test_b9_another_company_or_nonsense_is_unknown`, `::test_b9_a_refusal_in_any_answer_is_false`, and the B2 to B6 and D6 tests' `None` rows; `apps/core/tests/test_call_facts.py::test_the_company_matches_a_trade_name`; `apps/core/tests/test_worker.py::test_another_company_named_decides_as_the_call_would_without_it`, `::test_a_refusal_in_the_company_answer_goes_to_review` (Postgres) |
| B10 | `packages/decision/tests/test_rules.py::test_the_company_answer_is_not_required_to_verify`, `::test_v05_is_retired_and_its_ids_still_render`, `::test_nr03_has_its_new_sentence_and_is_the_company_answers_only_rule`; `test_engine.py` (the version); `tests/examples/22-another-company-named.json` |

## Landing

Two small PRs, each independent of the other: Part A (hints) and Part B (matcher, which Abhi
reviews). Part A edits the same `build_connect_twiml` attribute line as open PR #102, so whichever
merges second rebases and keeps both attributes.

## Out of scope

- Line-takeover detection (`line_risk`, NR21) and turning on strict mode (S01). Those are the real
  answers to a fraudster on the line (Goal); the gap is noted here, not fixed.
- Corrections with no refusal word, and other companies whose name contains the vendor's (D9; lab
  A8, A10). Negations, resellers and past connections are handled by D7a.
- Who fills `trade_names`.
- Changing the company question's wording, or the decision rules. (D7b changes the rules: V05 and
  NR03.)

## Open questions

- **Q136 (Sami, first live call):**
  - Does the speech-to-text provider honor `hints`, and is "Acme" now heard as "Acme"?
  - Which provider does this account use? The TwiML doesn't set `transcriptionProvider`, so the
    account default applies: Deepgram (nova-3), or Google for accounts that used ConversationRelay
    before Sep 12, 2025.
- **Q137 (Abhi):** accept Part B (D5 to D10), including its accepted costs (D9), with the
  measurement in D10 if it is run?
  - **Answered (D7b):** decided: demote, with NR03 kept for refusal words; Sami, Nov 10.
- **Q138 (Abhi, Sami):** should hints and the matcher use the vendor's name as it was before the
  change (a snapshot on the case), rather than Ramp's current record? A clerk can edit both in the
  same session.

## Commands

```bash
cd packages/policy && uv run pytest -q
cd apps/voice && uv run pytest -q
cd apps/core && uv run pytest -q tests/test_company.py
cd apps/core && uv run pytest -q tests/test_worker.py   # needs a throwaway Postgres
uv run --no-project --with ruff ruff check apps packages scripts corpus
python3 scripts/ci/boundaries.py
```
