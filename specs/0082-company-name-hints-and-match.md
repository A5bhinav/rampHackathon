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
8. **No fuzzy matching.** A one-letter slip rule was drafted and dropped after the builder check:
   - On sentences it accepts "help" for "Yelp", "this" for "Thin Air", and "Bell" for "Dell".
   - Phonetic codes disagree with each other: Soundex gives "acme" and "acne" the same code,
     Metaphone doesn't.
   - So "acne" stays `False` (NR03) unless Part A's hint fixes the transcript. That is a deliberate
     choice, pinned by a test.
9. **Accepted costs.**
   - A different company that shares a distinctive first word passes ("Acme Bolts" for "Acme
     Fasteners").
   - A negated or corrected short name passes ("Not Acme, this is Globex"). Today the same already
     happens with the full name ("Not Acme Fasteners, this is Globex"). The lab has measured this
     class of answer: `evals/FINDINGS.md` A8 and A10 on `sami/attack-lab-generative`.
   - Conflicting companies across answers are not flagged, because NR18 leaves companies to NR03
     and both answers match.
   - The digits must still match in every case.
10. **Part B is exploratory until measured.** These are the measurements, written before running
    anything. If the lab's company fuzzer (spec 0048) is run on the same seeds before and after
    Part B, report both numbers:
    - over-blocking, the share of answers that name the vendor but are not `True` (18.3%, 533 of
      2,920, in A10);
    - other-company acceptance, the answers naming someone else that come out `True`.

    The expectation is that over-blocking falls and other-company acceptance rises. That is the
    accepted cost in D9. Neither number gates the merge; both go to Abhi with Q137.

### Considered and rejected

- **Demote the check entirely.** It loses the "clearly different company, right digits" signal,
  which costs nothing to keep.
- **One-letter slips, and phonetic matching.** See D8.
- **The first word alone, whatever it is** (the first draft). On sentences it accepts "Hi, this is
  Globex" for "Thin Air" and "the main line at Globex" for "Main Street Supply". The guard's
  common-word and 4-character limits exist for exactly this.
- **Filling `trade_names`.** That is a sync change against Ramp's vendor fields, and its own spec.

## Contract touched

None. `company_matches` stays `bool | None`, and the decision rules don't change.

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
  These come from the builder check:

  | Said | Vendor |
  |---|---|
  | "Globex Corporation" | Acme Fasteners LLC |
  | "Hi, this is Globex, how can I help you?" | Yelp Inc |
  | "This is Globex" | Thin Air LLC |
  | "You've reached the main line at Globex" | Main Street Supply LLC |
  | "General Dynamics" | General Electric Company |
  | "Bell Canada" | Dell Inc |
  | "We're a plumbing firm, Globex" | A & B Supply |

- [ ] B3. No fuzzy matching (D8): "acne" and "This is acne" are `False`.
- [ ] B4. A common first word alone doesn't match: "First" is `False` for "First Solar Inc", and
  "Main" is `False` for "Main Street Supply LLC".
- [ ] B5. Unchanged:
  - "LLC" alone and no answers give `None`.
  - Any `False` among several answers gives `False`.
  - A vendor with no usable name gives today's result.
  - The existing tests in `apps/core/tests/test_worker.py` pass unedited.
- [ ] B6. The matcher reads names as the guard does: for each B1 example, the variant that matches
  is in `vendor_variants`.
- [ ] B7. The matcher stays pure and total. Empty, suffix-only and non-ASCII names, and empty
  answers, never raise.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| A1, A2 | `apps/voice/tests/test_twiml.py::test_hints_carry_the_vendor_names` (parametrized) |
| A3 | `apps/voice/tests/test_twiml.py::test_hints_are_escaped_and_read_back` |
| A4 | `apps/voice/tests/test_twiml.py::test_no_vendor_names_means_no_hints` |
| A5 | `apps/voice/tests/test_twiml.py::test_the_route_hints_the_calls_vendor_names_and_never_speaks_them` |
| A6 | the existing `packages/policy` and `apps/voice` suites, unedited |
| B1 to B7 | `apps/core/tests/test_company.py` (new, pure, no database) |
| B5 | the existing `apps/core/tests/test_worker.py` company tests, unedited (they need Postgres) |

## Landing

Two small PRs, each independent of the other: Part A (hints) and Part B (matcher, which Abhi
reviews). Part A edits the same `build_connect_twiml` attribute line as open PR #102, so whichever
merges second rebases and keeps both attributes.

## Out of scope

- Line-takeover detection (`line_risk`, NR21) and turning on strict mode (S01). Those are the real
  answers to a fraudster on the line (Goal); the gap is noted here, not fixed.
- Negations and corrections in the company answer (D9; lab A8, A10).
- Who fills `trade_names`.
- Changing the company question's wording, or the decision rules.

## Open questions

- **Q136 (Sami, first live call):**
  - Does the speech-to-text provider honor `hints`, and is "Acme" now heard as "Acme"?
  - Which provider does this account use? The TwiML doesn't set `transcriptionProvider`, so the
    account default applies: Deepgram (nova-3), or Google for accounts that used ConversationRelay
    before Sep 12, 2025.
- **Q137 (Abhi):** accept Part B (D5 to D10), including its accepted costs (D9), with the
  measurement in D10 if it is run?
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
